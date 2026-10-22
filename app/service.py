"""Logic nghiệp vụ của gateway — độc lập với MCP/HTTP để dễ kiểm thử."""
from __future__ import annotations

import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from pydantic import ValidationError

from app.config import JOB_ID_RE, Settings, redact
from app.github_client import GitHubClient, GitHubError
from app.job_store.base import JobStore, JobStoreError
from app.schemas import ACTIVE, TERMINAL, JobRecord, JobStatus, VideoJobRequest, utcnow_iso


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def new_job_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"job_{now:%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"


class JobService:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        gh: GitHubClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.s, self.store, self.gh = settings, store, gh
        self._sleep, self._clock = sleep, clock
        self._create_times: deque[float] = deque()

    # ------------------------------------------------------------------ helpers
    def _safe(self, text: str) -> str:
        return redact(text, self.s.secrets)

    def _load(self, job_id: str, owner: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
            raise ServiceError("invalid_job_id", "job_id không hợp lệ")
        try:
            rec = self.store.get_status(job_id)
        except JobStoreError as exc:
            raise ServiceError("store_error", self._safe(str(exc))) from None
        if rec is None or rec.get("owner", "default") != owner:  # không lộ job của người khác
            raise ServiceError("not_found", "Không tìm thấy job")
        return rec

    def _dispatch(self, job_id: str, attempt: int, project_type: str) -> None:
        if project_type == "video":
            self.gh.dispatch(job_id, attempt)  # lời gọi y hệt trước đây
        else:
            self.gh.dispatch(job_id, attempt, project_type=project_type)

    def _project_type(self, job_id: str) -> str:
        """project_type của job (request.json là bất biến). Request cũ không có trường này -> "video"."""
        try:
            req = self.store.get_request(job_id)
        except JobStoreError as exc:
            raise ServiceError("store_error", self._safe(str(exc))) from None
        return str((req or {}).get("project_type") or "video")

    def _find_run(self, rec: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """Trả về (run, cảnh báo). Ưu tiên run_id do worker ghi, sau đó tìm theo run-name."""
        try:
            if rec.get("run_id"):
                return self.gh.get_run(int(rec["run_id"])), None
            return self.gh.find_run(rec["job_id"], rec.get("attempt")), None
        except GitHubError as exc:
            return None, f"Không đối chiếu được với GitHub: {self._safe(str(exc))}"

    def _reconcile(self, rec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
        """Nếu workflow run đã kết thúc mà status.json vẫn 'đang chạy' (worker chết) -> suy ra trạng thái thật."""
        warnings: list[str] = []
        try:
            status = JobStatus(rec.get("status", "queued"))
        except ValueError:
            status = JobStatus.failed
        if status in TERMINAL:
            return rec, None, warnings
        run, warn = self._find_run(rec)
        if warn:
            warnings.append(warn)
        if run and run.get("status") == "completed":
            rec = dict(rec)
            concl = run.get("conclusion")
            if concl == "cancelled":
                rec.update(status="cancelled", stage="cancelled")
            elif concl != "success":
                rec.update(status="failed", stage="workflow_ended",
                           error=f"Workflow run kết thúc với conclusion={concl} trước khi worker báo hoàn tất")
            else:
                warnings.append("Run thành công nhưng status.json chưa cập nhật (có thể đang đồng bộ)")
            rec["reconciled"] = True
        return rec, run, warnings

    def _run_view(self, run: dict[str, Any] | None, rec: dict[str, Any]) -> dict[str, Any] | None:
        if run:
            return {"id": run["id"], "url": run["html_url"], "status": run["status"], "conclusion": run["conclusion"]}
        if rec.get("run_id"):
            return {"id": rec["run_id"], "url": rec.get("run_url")}
        return None

    # ------------------------------------------------------------------ tools
    def create_job(self, payload: dict[str, Any], owner: str = "default") -> dict[str, Any]:
        try:
            req = VideoJobRequest.model_validate(payload)
        except ValidationError as exc:
            errs = [f"{'.'.join(map(str, e['loc'])) or 'payload'}: {e['msg']}" for e in exc.errors()]
            raise ServiceError("invalid_payload", "; ".join(errs)[:900]) from None
        if req.duration_seconds > self.s.max_duration_seconds:
            raise ServiceError("invalid_payload", f"duration_seconds tối đa {self.s.max_duration_seconds}s (giới hạn runner miễn phí)")

        now = self._clock()
        while self._create_times and now - self._create_times[0] > 3600:
            self._create_times.popleft()
        if len(self._create_times) >= self.s.create_limit_per_hour:
            raise ServiceError("rate_limited", f"Tối đa {self.s.create_limit_per_hour} job/giờ, thử lại sau")

        job_id = new_job_id()
        record = JobRecord(job_id=job_id, owner=owner)
        try:
            stored = req.model_dump(mode="json")
            if req.project_type == "video":  # giữ nguyên định dạng request.json cũ cho job video
                stored.pop("project_type", None)
                stored.pop("render_profile", None)
            self.store.put_request(job_id, {**stored, "job_id": job_id, "owner": owner,
                                            "created_at": record.created_at})
            self.store.put_status(job_id, record.model_dump(mode="json"))
        except JobStoreError as exc:
            raise ServiceError("store_error", self._safe(str(exc))) from None

        self._create_times.append(now)
        try:
            self._dispatch(job_id, 1, req.project_type)
        except GitHubError as exc:
            msg = self._safe(str(exc))
            record.status, record.stage, record.error = JobStatus.failed, "dispatch_failed", msg
            record.updated_at = utcnow_iso()
            try:
                self.store.put_status(job_id, record.model_dump(mode="json"))
            except JobStoreError:
                pass
            raise ServiceError("dispatch_failed", f"Không kích hoạt được GitHub Actions: {msg}") from None

        note = ""
        if req.output_mode == "publish":
            note = " Chế độ publish: YouTube chỉ chạy nếu đã cấu hình OAuth trong GitHub Secrets; nếu chưa, video vẫn được lưu lên Hub."
        return {"ok": True, "job_id": job_id, "status": "queued", "project_type": req.project_type,
                "message": "Đã tạo job và kích hoạt GitHub Actions (chưa render xong). Dùng get_job_status để theo dõi." + note}

    def get_job_status(self, job_id: str, owner: str = "default") -> dict[str, Any]:
        rec = self._load(job_id, owner)
        rec, run, warnings = self._reconcile(rec)
        out = {
            "ok": True,
            "job_id": rec["job_id"],
            "status": rec.get("status"),
            "stage": rec.get("stage"),  # chỉ tên giai đoạn, không bịa phần trăm
            "attempt": rec.get("attempt", 1),
            "updated_at": rec.get("updated_at"),
            "error": rec.get("error"),
            "run": self._run_view(run, rec),
            "result_available": rec.get("status") == "completed" and bool(rec.get("result")),
        }
        if rec.get("reconciled"):
            out["reconciled"] = True
        if warnings:
            out["warnings"] = warnings
        if rec.get("status") == "completed":
            out["result"] = rec.get("result")
        return out

    def list_jobs(self, limit: int = 10, status: str | None = None, owner: str = "default") -> dict[str, Any]:
        limit = max(1, min(int(limit), 30))
        if status is not None and status not in {s.value for s in JobStatus}:
            raise ServiceError("invalid_payload", "status không hợp lệ")
        try:
            ids = self.store.list_job_ids()
        except JobStoreError as exc:
            raise ServiceError("store_error", self._safe(str(exc))) from None
        jobs: list[dict[str, Any]] = []
        for jid in ids:
            if len(jobs) >= limit:
                break
            try:
                rec = self.store.get_status(jid)
            except JobStoreError:
                continue
            if not rec or rec.get("owner", "default") != owner:
                continue
            if status and rec.get("status") != status:
                continue
            jobs.append({k: rec.get(k) for k in ("job_id", "status", "stage", "attempt", "created_at", "updated_at", "error")})
        return {"ok": True, "count": len(jobs), "jobs": jobs,
                "note": "Trạng thái lấy từ job store; dùng get_job_status để đối chiếu với GitHub."}

    def get_job_result(self, job_id: str, owner: str = "default") -> dict[str, Any]:
        rec = self._load(job_id, owner)
        rec, _run, warnings = self._reconcile(rec)
        if rec.get("status") != "completed" or not rec.get("result"):
            return {"ok": True, "ready": False, "job_id": job_id, "status": rec.get("status"),
                    "stage": rec.get("stage"), "error": rec.get("error"),
                    "message": "Job chưa có kết quả cuối cùng", **({"warnings": warnings} if warnings else {})}
        return {"ok": True, "ready": True, "job_id": job_id, "status": "completed", "result": rec["result"]}

    def retry_job(self, job_id: str, owner: str = "default") -> dict[str, Any]:
        rec = self._load(job_id, owner)
        rec, _run, _w = self._reconcile(rec)
        status = rec.get("status")
        if status not in (JobStatus.failed.value, JobStatus.cancelled.value):
            raise ServiceError("not_retryable", f"Chỉ retry job failed/cancelled (hiện tại: {status})")
        yt = ((rec.get("result") or {}).get("youtube") or {})
        if yt.get("state") == "uploaded" or yt.get("url"):
            raise ServiceError("duplicate_upload", "Job đã upload YouTube, từ chối retry để tránh đăng trùng")
        attempt = int(rec.get("attempt", 1)) + 1
        if attempt > 5:
            raise ServiceError("retry_limit", "Đã retry tối đa 5 lần")
        new = JobRecord(job_id=job_id, owner=owner, attempt=attempt,
                        created_at=rec.get("created_at") or utcnow_iso())
        data = new.model_dump(mode="json")
        data["previous_error"] = rec.get("error")
        project_type = self._project_type(job_id)  # đọc trước khi đổi trạng thái: lỗi store không để lại trạng thái dở
        try:
            self.store.put_status(job_id, data)
            self._dispatch(job_id, attempt, project_type)
        except JobStoreError as exc:
            raise ServiceError("store_error", self._safe(str(exc))) from None
        except GitHubError as exc:
            data.update(status="failed", stage="dispatch_failed", error=self._safe(str(exc)), updated_at=utcnow_iso())
            try:
                self.store.put_status(job_id, data)
            except JobStoreError:
                pass
            raise ServiceError("dispatch_failed", f"Không kích hoạt được GitHub Actions: {self._safe(str(exc))}") from None
        return {"ok": True, "job_id": job_id, "status": "queued", "attempt": attempt,
                "message": f"Đã chạy lại (lần {attempt}); thành phẩm lưu ở thư mục attempt-{attempt} riêng, không ghi đè bản cũ."}

    def cancel_job(self, job_id: str, owner: str = "default") -> dict[str, Any]:
        rec = self._load(job_id, owner)
        rec, run, _w = self._reconcile(rec)
        if rec.get("status") in {s.value for s in TERMINAL}:
            return {"ok": True, "cancelled": rec.get("status") == "cancelled", "status": rec.get("status"),
                    "message": f"Job đã kết thúc ({rec.get('status')}), không cần hủy"}
        if run is None:
            run, warn = self._find_run(rec)
            if warn:
                raise ServiceError("github_error", warn)
        if run is None:
            return {"ok": True, "cancelled": False, "cancel_requested": False, "status": rec.get("status"),
                    "message": "Chưa tìm thấy workflow run tương ứng (có thể chưa bắt đầu). Thử lại sau vài giây."}
        try:
            self.gh.cancel_run(int(run["id"]))
            confirmed = None
            for _ in range(5):  # chỉ báo 'đã hủy' khi GitHub xác nhận run completed/cancelled
                self._sleep(2)
                confirmed = self.gh.get_run(int(run["id"]))
                if confirmed.get("status") == "completed":
                    break
        except GitHubError as exc:
            raise ServiceError("github_error", self._safe(str(exc))) from None
        if confirmed and confirmed.get("status") == "completed":
            if confirmed.get("conclusion") == "cancelled":
                # `rec` là snapshot đọc từ trước lúc hủy: worker có thể đã ghi completed/cancelled hoặc job đã retry sang attempt mới
                # trong lúc chờ GitHub xác nhận. Đọc lại bản mới nhất và không ghi đè nếu nó đã đổi.
                try:
                    fresh = self.store.get_status(job_id) or rec
                except JobStoreError:
                    fresh = rec
                if (fresh.get("status") in {s.value for s in TERMINAL}
                        or int(fresh.get("attempt", 1)) != int(rec.get("attempt", 1))):
                    return {"ok": True, "cancelled": fresh.get("status") == "cancelled", "status": fresh.get("status"),
                            "message": "Trạng thái job đã được worker/retry cập nhật trong lúc hủy; giữ nguyên bản mới nhất"}
                rec = dict(fresh)
                rec.update(status="cancelled", stage="cancelled", updated_at=utcnow_iso(), run_id=run["id"])
                rec.pop("reconciled", None)
                try:
                    self.store.put_status(job_id, rec)
                except JobStoreError:
                    pass
                return {"ok": True, "cancelled": True, "status": "cancelled", "message": "GitHub xác nhận run đã bị hủy"}
            return {"ok": True, "cancelled": False, "status": rec.get("status"),
                    "message": f"Run đã kết thúc với conclusion={confirmed.get('conclusion')} trước khi hủy kịp"}
        return {"ok": True, "cancelled": False, "cancel_requested": True, "status": rec.get("status"),
                "message": "Đã gửi yêu cầu hủy nhưng GitHub chưa xác nhận. Kiểm tra lại bằng get_job_status."}

    def system_status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "gateway": {"state": "ok", "time": utcnow_iso()}}
        try:
            out["github"] = {"state": "ok", **self.gh.ping()}
            runs = self.gh.recent_runs(5)
            out["recent_runs"] = [{"id": r["id"], "title": r["display_title"][:80], "status": r["status"],
                                   "conclusion": r["conclusion"]} for r in runs]
        except GitHubError as exc:
            out["github"] = {"state": "error", "detail": self._safe(str(exc))}
        out["job_store"] = self._probe_store()
        out["hugging_face"] = self._probe_hf()
        out["youtube"] = {"state": "not_checked",
                          "detail": "Secret YouTube nằm trong GitHub Secrets; gateway không đọc được. Xem log workflow."}
        out["ok"] = all(out[k].get("state") == "ok" for k in ("github", "job_store"))
        return out

    def _probe_store(self) -> dict[str, Any]:
        try:
            self.store.list_job_ids()
            return {"state": "ok", "backend": self.s.store_backend}
        except JobStoreError as exc:
            return {"state": "error", "backend": self.s.store_backend, "detail": self._safe(str(exc))}

    def _probe_hf(self) -> dict[str, Any]:
        if self.s.store_backend != "hub":
            return {"state": "skipped", "detail": "store_backend=local"}
        try:
            r = requests.get("https://huggingface.co/api/whoami-v2",
                             headers={"Authorization": f"Bearer {self.s.hf_token}"}, timeout=(5, 10))
        except requests.RequestException as exc:
            return {"state": "error", "detail": type(exc).__name__}
        return {"state": "ok" if r.status_code == 200 else "error",
                **({} if r.status_code == 200 else {"detail": f"HTTP {r.status_code}"})}
