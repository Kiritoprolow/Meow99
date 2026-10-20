"""Cấu hình gateway, đọc từ biến môi trường. Không chứa giá trị bí mật mặc định."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

JOB_ID_RE = re.compile(r"^job_\d{8}_\d{6}_[0-9a-f]{6}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # không in giá trị, chỉ tên biến
        raise ValueError(f"Biến môi trường {name} phải là số nguyên") from exc


@dataclass(frozen=True)
class Settings:
    # --- xác thực gateway ---
    mcp_api_key: str = ""          # Bearer token (Claude, Cursor, curl...)
    mcp_path_secret: str = ""      # secret trong URL cho ChatGPT "No authentication"
    allow_no_auth: bool = False    # chỉ dùng khi dev cục bộ

    # --- GitHub Actions ---
    github_token: str = ""
    github_repo: str = ""          # owner/repo
    github_workflow: str = "render.yml"
    github_ref: str = "main"

    # --- Hugging Face Hub ---
    hf_token: str = ""
    hf_state_repo: str = ""        # dataset repo lưu request.json / status.json
    hf_output_repo: str = ""       # dataset repo lưu thành phẩm

    # --- job store ---
    store_backend: str = "hub"     # hub | local
    local_store_dir: str = "./.local_store"

    # --- giới hạn ---
    max_duration_seconds: int = 120
    rate_limit_per_min: int = 60
    create_limit_per_hour: int = 10
    max_body_bytes: int = 64 * 1024

    secrets: tuple[str, ...] = field(default=(), repr=False)

    @property
    def state_repo(self) -> str:
        return self.hf_state_repo or self.hf_output_repo

    @classmethod
    def from_env(cls) -> "Settings":
        g = lambda k, d="": os.environ.get(k, d).strip()  # noqa: E731
        s = cls(
            mcp_api_key=g("MCP_API_KEY"),
            mcp_path_secret=g("MCP_PATH_SECRET"),
            allow_no_auth=g("ALLOW_NO_AUTH").lower() in {"1", "true", "yes"},
            github_token=g("GITHUB_TOKEN"),
            github_repo=g("GITHUB_REPO"),
            github_workflow=g("GITHUB_WORKFLOW", "render.yml"),
            github_ref=g("GITHUB_REF_NAME", g("GITHUB_REF", "main")) or "main",
            hf_token=g("HF_TOKEN"),
            hf_state_repo=g("HF_STATE_REPO"),
            hf_output_repo=g("HF_OUTPUT_REPO"),
            store_backend=g("STORE_BACKEND", "hub").lower(),
            local_store_dir=g("LOCAL_STORE_DIR", "./.local_store"),
            max_duration_seconds=_int("MAX_DURATION_SECONDS", 120),
            rate_limit_per_min=_int("RATE_LIMIT_PER_MIN", 60),
            create_limit_per_hour=_int("CREATE_LIMIT_PER_HOUR", 10),
            secrets=tuple(
                v for v in (g("MCP_API_KEY"), g("MCP_PATH_SECRET"), g("GITHUB_TOKEN"), g("HF_TOKEN")) if v
            ),
        )
        return s

    def validate_for_gateway(self) -> list[str]:
        """Trả về danh sách lỗi cấu hình (rỗng = ổn). Không chứa giá trị bí mật."""
        errs: list[str] = []
        if not (self.mcp_api_key or self.mcp_path_secret or self.allow_no_auth):
            errs.append("Cần MCP_API_KEY hoặc MCP_PATH_SECRET (hoặc ALLOW_NO_AUTH=true khi dev)")
        for name, val, minlen in (("MCP_API_KEY", self.mcp_api_key, 24), ("MCP_PATH_SECRET", self.mcp_path_secret, 24)):
            if val and len(val) < minlen:
                errs.append(f"{name} phải dài tối thiểu {minlen} ký tự")
        if self.mcp_path_secret and not re.fullmatch(r"[A-Za-z0-9_-]+", self.mcp_path_secret):
            errs.append("MCP_PATH_SECRET chỉ được chứa A-Z a-z 0-9 _ -")
        if self.store_backend not in {"hub", "local"}:
            errs.append("STORE_BACKEND phải là hub hoặc local")
        if self.store_backend == "hub":
            if not self.hf_token:
                errs.append("Thiếu HF_TOKEN")
            if not self.state_repo or not REPO_RE.match(self.state_repo):
                errs.append("Thiếu hoặc sai HF_STATE_REPO/HF_OUTPUT_REPO (dạng user/name)")
        if not self.github_token:
            errs.append("Thiếu GITHUB_TOKEN")
        if not self.github_repo or not REPO_RE.match(self.github_repo):
            errs.append("Thiếu hoặc sai GITHUB_REPO (dạng owner/repo)")
        return errs


def redact(text: str, secrets: tuple[str, ...] | list[str]) -> str:
    """Xóa mọi giá trị bí mật khỏi chuỗi trước khi log hoặc trả về."""
    out = text
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***")
    return out
