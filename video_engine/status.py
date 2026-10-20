"""Báo cáo trạng thái job ra job store (status.json). Chỉ worker ghi sau khi job bắt đầu."""
from __future__ import annotations

import logging
from typing import Any

from app.job_store.base import JobStore, JobStoreError
from app.schemas import JobStatus, utcnow_iso

log = logging.getLogger("video_engine.status")


class StatusReporter:
    def __init__(self, store: JobStore, job_id: str, attempt: int, run_id: int | None = None, run_url: str | None = None):
        self.store, self.job_id = store, job_id
        existing = store.get_status(job_id) or {}
        self.rec: dict[str, Any] = {**existing, "job_id": job_id, "attempt": attempt,
                                    "run_id": run_id, "run_url": run_url, "error": None}
        self.rec.setdefault("owner", "default")
        self.rec.setdefault("created_at", utcnow_iso())

    def update(self, status: JobStatus, stage: str, *, error: str | None = None,
               result: dict[str, Any] | None = None, critical: bool = False) -> None:
        self.rec.update(status=status.value, stage=stage, updated_at=utcnow_iso(), error=error)
        if result is not None:
            self.rec["result"] = result
        try:
            self.store.put_status(self.job_id, self.rec)
        except JobStoreError as exc:
            if critical:
                raise
            log.warning("Không ghi được trạng thái giai đoạn %s: %s", stage, exc)
