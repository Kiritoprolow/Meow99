"""Giao diện job store bền vững.

Quy ước để tránh ghi đè lẫn nhau giữa gateway và worker:
  jobs/<job_id>/request.json  — gateway ghi MỘT lần (immutable)
  jobs/<job_id>/status.json   — gateway ghi lúc tạo/retry, sau đó chỉ worker ghi
"""
from __future__ import annotations

from typing import Any, Protocol

from app.config import JOB_ID_RE


class JobStoreError(RuntimeError):
    """Lỗi lưu trữ (thông điệp không chứa secret)."""


def check_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise JobStoreError("job_id không hợp lệ")
    return job_id


def request_path(job_id: str) -> str:
    return f"jobs/{check_job_id(job_id)}/request.json"


def status_path(job_id: str) -> str:
    return f"jobs/{check_job_id(job_id)}/status.json"


class JobStore(Protocol):
    def put_request(self, job_id: str, request: dict[str, Any]) -> None: ...
    def get_request(self, job_id: str) -> dict[str, Any] | None: ...
    def put_status(self, job_id: str, status: dict[str, Any]) -> None: ...
    def get_status(self, job_id: str) -> dict[str, Any] | None: ...
    def list_job_ids(self) -> list[str]: ...
