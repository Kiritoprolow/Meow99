"""Middleware ASGI: xác thực, giới hạn tần suất và kích thước body cho endpoint MCP.

Hai cách xác thực (cấu hình ít nhất một):
  1. Header `Authorization: Bearer <MCP_API_KEY>`  -> URL  /mcp
  2. Secret trong đường dẫn (cho ChatGPT, vốn chỉ có OAuth hoặc 'No authentication') -> URL /<MCP_PATH_SECRET>/mcp

Lưu ý: KHÔNG ghi log đường dẫn (secret nằm trong URL) — chạy uvicorn với access_log=False.
"""
from __future__ import annotations

import hmac
import json
import re
import time
from collections import deque
from typing import Any

from app.config import Settings

_SECRET_PATH = re.compile(r"^/([A-Za-z0-9_-]{24,})/mcp/?$")


class SlidingWindow:
    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        while self._hits and now - self._hits[0] > self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class AuthRateLimitMiddleware:
    def __init__(self, app: Any, settings: Settings):
        self.app, self.s = app, settings
        self._ok = SlidingWindow(settings.rate_limit_per_min, 60.0)
        self._bad = SlidingWindow(30, 60.0)  # chặn dò secret

    async def _send(self, send: Any, status: int, body: dict[str, Any] | str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        if isinstance(body, str):
            payload, ctype = body.encode(), b"text/plain; charset=utf-8"
        else:
            payload, ctype = json.dumps(body).encode(), b"application/json"
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"content-length", str(len(payload)).encode()), *(headers or [])]})
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan, websocket...
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path == "/healthz":
            await self._send(send, 200, {"status": "ok"})
            return
        if path == "/":
            await self._send(send, 200, "AI Video Studio MCP gateway")
            return

        authed, new_scope = False, scope
        m = _SECRET_PATH.match(path)
        if m and self.s.mcp_path_secret:
            if _eq(m.group(1), self.s.mcp_path_secret):
                authed = True
                new_scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        elif path.rstrip("/") == "/mcp":
            hdrs = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = hdrs.get(b"authorization", b"").decode("latin-1")
            if self.s.mcp_api_key and auth.startswith("Bearer ") and _eq(auth[7:].strip(), self.s.mcp_api_key):
                authed = True
            elif self.s.allow_no_auth and not self.s.mcp_api_key and not self.s.mcp_path_secret:
                authed = True

        if not authed:
            if not self._bad.allow():
                await self._send(send, 429, {"error": "too_many_requests"}, [(b"retry-after", b"60")])
                return
            if _SECRET_PATH.match(path) or path.rstrip("/") == "/mcp":
                await self._send(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b'Bearer realm="mcp"')])
            else:
                await self._send(send, 404, {"error": "not_found"})
            return

        if not self._ok.allow():
            await self._send(send, 429, {"error": "rate_limited"}, [(b"retry-after", b"30")])
            return

        if scope.get("method") == "POST":
            hdrs = {k.lower(): v for k, v in scope.get("headers", [])}
            cl = hdrs.get(b"content-length")
            if cl is None:
                await self._send(send, 411, {"error": "content_length_required"})
                return
            try:
                too_big = int(cl) > self.s.max_body_bytes
            except ValueError:
                too_big = True
            if too_big:
                await self._send(send, 413, {"error": "payload_too_large"})
                return

        await self.app(new_scope, receive, send)
