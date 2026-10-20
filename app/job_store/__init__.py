from __future__ import annotations

from app.config import Settings
from app.job_store.base import JobStore, JobStoreError
from app.job_store.hub_store import HubJobStore
from app.job_store.local_store import LocalJobStore


def make_store(settings: Settings) -> JobStore:
    if settings.store_backend == "local":
        return LocalJobStore(settings.local_store_dir)
    return HubJobStore(settings.hf_token, settings.state_repo)


__all__ = ["JobStore", "JobStoreError", "HubJobStore", "LocalJobStore", "make_store"]
