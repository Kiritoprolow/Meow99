"""Client GitHub Actions tối giản: dispatch, tìm run theo job_id, hủy run.

Ánh xạ job_id -> workflow run:
  * workflow đặt `run-name: video-job <job_id> #<attempt>`; gateway tìm run có display_title chứa job_id.
  * worker cũng ghi run_id vào status.json ngay khi bắt đầu (nguồn chính xác nhất).
"""
from __future__ import annotations

from typing import Any

import requests

API = "https://api.github.com"
TIMEOUT = (5, 20)


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _norm(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "status": run.get("status"),          # queued | in_progress | completed
        "conclusion": run.get("conclusion"),  # success | failure | cancelled | timed_out | None
        "html_url": run.get("html_url"),
        "created_at": run.get("created_at"),
        "display_title": run.get("display_title") or run.get("name") or "",
    }


class GitHubClient:
    def __init__(self, token: str, repo: str, workflow: str, ref: str = "main", session: requests.Session | None = None):
        if not token or not repo:
            raise GitHubError("Thiếu GITHUB_TOKEN hoặc GITHUB_REPO")
        self._token, self.repo, self.workflow, self.ref = token, repo, workflow, ref
        self._s = session or requests.Session()

    def _req(self, method: str, path: str, **kw: Any) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            r = self._s.request(method, f"{API}{path}", headers=headers, timeout=TIMEOUT, **kw)
        except requests.RequestException as exc:
            raise GitHubError(f"Không gọi được GitHub API: {type(exc).__name__}") from None
        if r.status_code == 401:
            raise GitHubError("GITHUB_TOKEN không hợp lệ hoặc hết hạn", 401)
        if r.status_code == 403:
            if r.headers.get("x-ratelimit-remaining") == "0":
                raise GitHubError("GitHub API đang bị rate limit, thử lại sau", 403)
            raise GitHubError("GITHUB_TOKEN thiếu quyền (cần Actions: read & write)", 403)
        if r.status_code == 404:
            raise GitHubError("Không thấy repo/workflow/run (sai GITHUB_REPO, tên workflow hoặc thiếu quyền)", 404)
        return r

    def dispatch(self, job_id: str, attempt: int) -> None:
        r = self._req(
            "POST",
            f"/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches",
            json={"ref": self.ref, "inputs": {"job_id": job_id, "attempt": str(attempt)}},
        )
        if r.status_code != 204:
            raise GitHubError(f"Dispatch thất bại (HTTP {r.status_code}); kiểm tra workflow có trigger workflow_dispatch", r.status_code)

    def recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        r = self._req(
            "GET",
            f"/repos/{self.repo}/actions/workflows/{self.workflow}/runs",
            params={"event": "workflow_dispatch", "per_page": min(max(limit, 1), 100)},
        )
        if r.status_code != 200:
            raise GitHubError(f"Liệt kê run thất bại (HTTP {r.status_code})", r.status_code)
        return [_norm(x) for x in r.json().get("workflow_runs", [])]

    def find_run(self, job_id: str, attempt: int | None = None) -> dict[str, Any] | None:
        """Run mới nhất có display_title chứa job_id (và đúng attempt nếu cho)."""
        needle = f"{job_id} #{attempt}" if attempt else job_id
        matches = [x for x in self.recent_runs(50) if needle in x["display_title"]]
        return max(matches, key=lambda x: x.get("created_at") or "") if matches else None

    def get_run(self, run_id: int) -> dict[str, Any]:
        r = self._req("GET", f"/repos/{self.repo}/actions/runs/{int(run_id)}")
        if r.status_code != 200:
            raise GitHubError(f"Đọc run thất bại (HTTP {r.status_code})", r.status_code)
        return _norm(r.json())

    def cancel_run(self, run_id: int) -> None:
        r = self._req("POST", f"/repos/{self.repo}/actions/runs/{int(run_id)}/cancel")
        if r.status_code not in (202, 409):  # 409: run đã kết thúc
            raise GitHubError(f"Hủy run thất bại (HTTP {r.status_code})", r.status_code)

    def ping(self) -> dict[str, Any]:
        r = self._req("GET", f"/repos/{self.repo}/actions/workflows/{self.workflow}")
        if r.status_code != 200:
            raise GitHubError(f"HTTP {r.status_code}", r.status_code)
        return {"workflow_state": r.json().get("state")}
