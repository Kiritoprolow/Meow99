"""Pipeline: request -> engine (slideshow | blender_3d) -> metadata -> QC -> upload -> báo cáo.

Engine (video_engine.engines) chỉ tạo nội dung; phần dùng chung (metadata, QC, upload, trạng thái job) ở đây.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.config import redact
from app.job_store.base import JobStore
from app.schemas import JobStatus, VideoJobRequest
from video_engine.engines import RenderContext, get_engine
from video_engine.engines.base import StageError  # noqa: F401  (re-export: code cũ import từ đây)
from video_engine.metadata.meta import build_metadata
from video_engine.quality_control.qc import run_qc
from video_engine.status import StatusReporter
from video_engine.storage.outputs import OutputStorage
from video_engine.visuals.slides import frame_size
from video_engine.youtube.publish import plan_youtube

log = logging.getLogger("video_engine")
_INTERNAL_KEYS = {"job_id", "owner", "created_at"}


class Cancelled(Exception):
    """Ném ra khi nhận SIGTERM/SIGINT (GitHub hủy run)."""


def collect_secrets(env: Mapping[str, str]) -> list[str]:
    return [v for k, v in env.items() if v and len(v) >= 6 and any(t in k.upper() for t in ("TOKEN", "SECRET", "KEY", "PASSWORD"))]


def run_job(job_id: str, attempt: int, store: JobStore, storage: OutputStorage, env: Mapping[str, str] | None = None,
            workdir: Path | None = None) -> dict[str, Any]:
    """Chạy một job. Trả về status.json cuối cùng. Không raise trừ lỗi ghi trạng thái cuối cùng."""
    env = env if env is not None else os.environ
    secrets = collect_secrets(env)
    run_id = int(env["GITHUB_RUN_ID"]) if str(env.get("GITHUB_RUN_ID", "")).isdigit() else None
    run_url = None
    if run_id and env.get("GITHUB_REPOSITORY"):
        run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env['GITHUB_REPOSITORY']}/actions/runs/{run_id}"
    prev = store.get_status(job_id) or {}
    if prev.get("status") == JobStatus.completed.value and int(prev.get("attempt", 1)) == attempt:
        # dispatch đúp / re-run: attempt này đã hoàn tất -> không đụng vào kết quả (không render, không đánh dấu failed)
        log.warning("Job %s attempt %s đã hoàn tất, bỏ qua lần chạy trùng", job_id, attempt)
        return prev
    rep = StatusReporter(store, job_id, attempt, run_id, run_url)
    own_tmp = workdir is None
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=f"{job_id}_a{attempt}_"))  # riêng theo job + attempt
    wd.mkdir(parents=True, exist_ok=True)
    stage = "loading_request"

    def progress(name: str) -> None:
        nonlocal stage
        stage = name
        rep.update(JobStatus.running, name)

    try:
        rep.update(JobStatus.running, stage)
        raw = store.get_request(job_id)
        if raw is None:
            raise StageError(stage, "Không tìm thấy request.json của job")
        req = VideoJobRequest.model_validate({k: v for k, v in raw.items() if k not in _INTERNAL_KEYS})
        r = req.model_dump()
        size = frame_size(r["aspect_ratio"], r["resolution"])
        engine = get_engine(r["project_type"])

        out = engine.render(RenderContext(job_id=job_id, attempt=attempt, request=r, workdir=wd, size=size,
                                          progress=progress, env=env))
        video, thumb = out.video, out.thumbnail

        stage = "metadata"; rep.update(JobStatus.running, stage)
        meta = build_metadata(r, out.script_source, job_id)
        for k, v in out.metadata_overrides.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        meta_path = wd / "metadata.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        stage = "quality_control"; rep.update(JobStatus.running, stage)
        qc = run_qc(video, size, float(r["duration_seconds"]), thumb, meta, **out.qc_options)
        if not qc["ok"]:
            failed = [f"{c['name']} ({c['detail']})" for c in qc["checks"] if not c["ok"]]
            raise StageError(stage, "QC thất bại, không upload: " + "; ".join(failed))

        notes = list(out.notes)
        youtube = plan_youtube(r, env)

        report = {"job_id": job_id, "attempt": attempt, "project_type": r["project_type"], "engine": engine.name,
                  "script_source": out.script_source, "qc": qc, "frame_size": list(size), "segments": out.segments,
                  "youtube": youtube, "notes": notes}
        if out.report:
            report["engine_report"] = out.report
        report_path = wd / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        files = {"video.mp4": video, "metadata.json": meta_path, "report.json": report_path, **out.files}
        if thumb:
            files["thumbnail.jpg"] = thumb

        stage = "uploading_to_hf"; rep.update(JobStatus.uploading_to_hf, stage)
        up = storage.upload(job_id, attempt, files)

        urls = up["urls"]
        result = {
            "repo_id": up["repo_id"], "revision": up["revision"], "access": up["access"], "prefix": up["prefix"],
            "video_url": urls.get("video.mp4"), "thumbnail_url": urls.get("thumbnail.jpg"),
            "metadata_url": urls.get("metadata.json"), "captions_url": urls.get("captions.srt"),
            "script_url": urls.get("script.txt"), "report_url": urls.get("report.json"),
            "files": up["files"], "youtube": youtube, "script_source": out.script_source,
            "qc": {"ok": True, **qc["probe"]}, "notes": notes,
        }
        if r["project_type"] != "video":  # khóa bổ sung, chỉ xuất hiện với job không phải video thường
            result["project_type"], result["engine"] = r["project_type"], engine.name
            if "scene.blend" in urls:
                result["scene_blend_url"] = urls["scene.blend"]
            if "frames.zip" in urls:
                result["frames_url"] = urls["frames.zip"]
        if up["access"] == "private":
            result["access_note"] = "Repo private: URL chỉ mở được khi đăng nhập Hugging Face có quyền truy cập."
        rep.update(JobStatus.completed, "done", result=result, critical=True)
        return rep.rec
    except Cancelled:
        rep.update(JobStatus.cancelled, "cancelled", critical=False)
        return rep.rec
    except Exception as exc:  # noqa: BLE001
        where = exc.stage if isinstance(exc, StageError) else stage
        msg = redact(f"{type(exc).__name__}: {exc}" if not isinstance(exc, StageError) else str(exc), secrets)
        log.error("Job %s lỗi ở giai đoạn %s: %s", job_id, where, msg)
        rep.update(JobStatus.failed, where, error=msg[:1500], critical=False)
        return rep.rec
    finally:
        if own_tmp:
            shutil.rmtree(wd, ignore_errors=True)
