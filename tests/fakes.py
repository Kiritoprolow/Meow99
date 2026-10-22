from __future__ import annotations

import tempfile
from typing import Any

from app.config import Settings
from app.github_client import GitHubError
from app.job_store import LocalJobStore
from app.service import JobService

API_KEY = "k" * 32
PATH_SECRET = "s" * 32


class FakeGitHub:
    """GitHub giả: ghi lại dispatch, cho phép lập trình trạng thái run."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, int]] = []
        self.dispatched_types: list[str] = []  # project_type của từng lần dispatch (song song với `dispatched`)
        self.runs: dict[int, dict[str, Any]] = {}
        self.fail_dispatch: GitHubError | None = None
        self.cancel_result = "cancelled"  # cancelled | stuck
        self.cancel_calls: list[int] = []
        self._next = 1000

    def dispatch(self, job_id: str, attempt: int, project_type: str = "video") -> None:
        if self.fail_dispatch:
            raise self.fail_dispatch
        self.dispatched.append((job_id, attempt))
        self.dispatched_types.append(project_type)
        self._next += 1
        self.runs[self._next] = {"id": self._next, "status": "queued", "conclusion": None,
                                 "html_url": f"https://github.com/o/r/actions/runs/{self._next}",
                                 "created_at": f"2026-10-20T00:00:{self._next % 60:02d}Z",
                                 "display_title": f"video-job {job_id} #{attempt}"}

    def recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        return list(self.runs.values())

    def find_run(self, job_id: str, attempt: int | None = None) -> dict[str, Any] | None:
        needle = f"{job_id} #{attempt}" if attempt else job_id
        m = [r for r in self.runs.values() if needle in r["display_title"]]
        return max(m, key=lambda r: r["created_at"]) if m else None

    def get_run(self, run_id: int) -> dict[str, Any]:
        if run_id not in self.runs:
            raise GitHubError("Không thấy run", 404)
        return self.runs[run_id]

    def cancel_run(self, run_id: int) -> None:
        self.cancel_calls.append(run_id)
        if self.cancel_result == "cancelled":
            self.runs[run_id].update(status="completed", conclusion="cancelled")

    def ping(self) -> dict[str, Any]:
        return {"workflow_state": "active"}

    def finish(self, run_id: int, conclusion: str) -> None:
        self.runs[run_id].update(status="completed", conclusion=conclusion)


def make_settings(**over: Any) -> Settings:
    base = dict(mcp_api_key=API_KEY, mcp_path_secret=PATH_SECRET, store_backend="local",
                local_store_dir=tempfile.mkdtemp(), create_limit_per_hour=100,
                secrets=(API_KEY, PATH_SECRET, "ghp_SECRETTOKEN123", "hf_SECRETTOKEN123"))
    base.update(over)
    return Settings(**base)


def make_service(**over: Any) -> tuple[JobService, FakeGitHub, LocalJobStore]:
    s = make_settings(**over)
    store = LocalJobStore(s.local_store_dir)
    gh = FakeGitHub()
    return JobService(s, store, gh, sleep=lambda _x: None), gh, store  # type: ignore[arg-type]
