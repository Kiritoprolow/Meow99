"""Job store trên đĩa — dùng cho dev/test. KHÔNG dùng trên Space (đĩa mất khi restart)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.config import JOB_ID_RE
from app.job_store.base import JobStoreError, request_path, status_path


class LocalJobStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents:
            raise JobStoreError("đường dẫn ngoài thư mục store")
        return p

    def _write(self, rel: str, obj: dict[str, Any], create_only: bool = False) -> None:
        p = self._p(rel)
        if create_only and p.exists():
            raise JobStoreError("file đã tồn tại, từ chối ghi đè")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)  # ghi nguyên tử
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _read(self, rel: str) -> dict[str, Any] | None:
        p = self._p(rel)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def put_request(self, job_id: str, request: dict[str, Any]) -> None:
        self._write(request_path(job_id), request, create_only=True)

    def get_request(self, job_id: str) -> dict[str, Any] | None:
        return self._read(request_path(job_id))

    def put_status(self, job_id: str, status: dict[str, Any]) -> None:
        self._write(status_path(job_id), status)

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        return self._read(status_path(job_id))

    def list_job_ids(self) -> list[str]:
        base = self.root / "jobs"
        if not base.exists():
            return []
        return sorted((d.name for d in base.iterdir() if d.is_dir() and JOB_ID_RE.match(d.name)), reverse=True)
