"""Lắp ráp ứng dụng ASGI của gateway: MCP (Streamable HTTP, stateless) + middleware bảo mật."""
from __future__ import annotations

from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

from app.config import Settings
from app.gateway.auth import AuthRateLimitMiddleware
from app.github_client import GitHubClient
from app.job_store import make_store
from app.mcp_server.server import build_mcp
from app.service import JobService


def build_service(settings: Settings) -> JobService:
    return JobService(
        settings,
        make_store(settings),
        GitHubClient(settings.github_token, settings.github_repo, settings.github_workflow, settings.github_ref),
    )


def build_app(settings: Settings | None = None, service: JobService | None = None) -> Any:
    settings = settings or Settings.from_env()
    errs = settings.validate_for_gateway()
    if errs and service is None:
        raise RuntimeError("Cấu hình gateway chưa đủ:\n- " + "\n- ".join(errs))
    service = service or build_service(settings)
    mcp = build_mcp(service)
    inner = mcp.streamable_http_app(
        stateless_http=True,          # không giữ session trong RAM -> an toàn khi Space restart/ngủ
        json_response=True,
        max_request_body_size=settings.max_body_bytes,
        # Host trên Space là *.hf.space nên bảo vệ DNS-rebinding mặc định (chỉ cho localhost) sẽ chặn nhầm.
        # Truy cập đã được bảo vệ bằng secret/Bearer ở middleware phía trên.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return AuthRateLimitMiddleware(inner, settings)
