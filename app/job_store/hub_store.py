"""Job store trên Hugging Face Hub (dataset repo) — bền vững qua restart Space.

Đọc bằng HTTP (requests, không cache), ghi bằng huggingface_hub (import trễ).
CHƯA được kiểm thử với Hub thật trong môi trường phát triển (xem docs/TESTING.md).
"""
from __future__ import annotations

import json
from typing import Any

import requests

from app.job_store.base import JobStoreError, check_job_id, request_path, status_path

HF = "https://huggingface.co"
TIMEOUT = (5, 20)


class HubJobStore:
    def __init__(self, token: str, repo_id: str, session: requests.Session | None = None):
        if not token or not repo_id:
            raise JobStoreError("Thiếu HF_TOKEN hoặc HF_STATE_REPO")
        self._token = token
        self.repo_id = repo_id
        self._s = session or requests.Session()

    # --- helpers ---
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Cache-Control": "no-cache"}

    def _get(self, url: str, **kw: Any) -> requests.Response:
        try:
            return self._s.get(url, headers=self._headers(), timeout=TIMEOUT, **kw)
        except requests.RequestException as exc:
            raise JobStoreError(f"Không gọi được Hugging Face Hub: {type(exc).__name__}") from None

    def _read(self, rel: str) -> dict[str, Any] | None:
        r = self._get(f"{HF}/datasets/{self.repo_id}/resolve/main/{rel}")
        if r.status_code == 404:
            return None
        if r.status_code in (401, 403):
            raise JobStoreError("HF_TOKEN không có quyền đọc state repo")
        if r.status_code != 200:
            raise JobStoreError(f"Hub trả về HTTP {r.status_code} khi đọc state")
        try:
            return json.loads(r.content.decode("utf-8"))
        except ValueError:
            raise JobStoreError("File state trên Hub không phải JSON hợp lệ") from None

    def _write(self, rel: str, obj: dict[str, Any], create_only: bool) -> None:
        try:
            from huggingface_hub import HfApi  # import trễ
        except ImportError as exc:
            raise JobStoreError("Thiếu thư viện huggingface_hub") from exc
        api = HfApi(token=self._token)
        try:
            if create_only and api.file_exists(self.repo_id, rel, repo_type="dataset"):
                raise JobStoreError("file đã tồn tại, từ chối ghi đè")
            api.upload_file(
                path_or_fileobj=json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"),
                path_in_repo=rel,
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"state: {rel}",
            )
        except JobStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - không để lộ chi tiết có thể chứa token
            raise JobStoreError(f"Ghi state lên Hub thất bại: {type(exc).__name__}") from None

    # --- API ---
    def put_request(self, job_id: str, request: dict[str, Any]) -> None:
        self._write(request_path(job_id), request, create_only=True)

    def get_request(self, job_id: str) -> dict[str, Any] | None:
        return self._read(request_path(job_id))

    def put_status(self, job_id: str, status: dict[str, Any]) -> None:
        self._write(status_path(job_id), status, create_only=False)

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        return self._read(status_path(job_id))

    def list_job_ids(self) -> list[str]:
        r = self._get(f"{HF}/api/datasets/{self.repo_id}/tree/main/jobs")
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise JobStoreError(f"Hub trả về HTTP {r.status_code} khi liệt kê job")
        try:
            entries = r.json()
        except ValueError:
            raise JobStoreError("Phản hồi liệt kê không phải JSON") from None
        ids = []
        for e in entries:
            name = str(e.get("path", "")).rsplit("/", 1)[-1]
            try:
                ids.append(check_job_id(name))
            except JobStoreError:
                continue
        return sorted(ids, reverse=True)
