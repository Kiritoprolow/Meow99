"""Pipeline MVP: request -> script -> slides -> FFmpeg -> thumbnail -> metadata -> QC -> upload -> báo cáo."""
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
from video_engine.metadata.meta import build_metadata
from video_engine.quality_control.qc import run_qc
from video_engine.renderer.ffmpeg_renderer import concat_with_audio, render_clip
from video_engine.script.generator import generate
from video_engine.status import StatusReporter
from video_engine.storage.outputs import OutputStorage
from video_engine.subtitles.srt import write_srt
from video_engine.thumbnail.thumb import make_thumbnail
from video_engine.visuals.slides import frame_size, render_slide
from video_engine.youtube.publish import plan_youtube

log = logging.getLogger("video_engine")
_INTERNAL_KEYS = {"job_id", "owner", "created_at"}


class Cancelled(Exception):
    """Ném ra khi nhận SIGTERM/SIGINT (GitHub hủy run)."""


class StageError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


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
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix=f"{job_id}_"))
    wd.mkdir(parents=True, exist_ok=True)
    stage = "loading_request"
    try:
        rep.update(JobStatus.running, stage)
        raw = store.get_request(job_id)
        if raw is None:
            raise StageError(stage, "Không tìm thấy request.json của job")
        req = VideoJobRequest.model_validate({k: v for k, v in raw.items() if k not in _INTERNAL_KEYS})
        r = req.model_dump()

        stage = "script"; rep.update(JobStatus.running, stage)
        segments, script_source = generate(r["topic"], r["language"], r["duration_seconds"], r["script"])
        script_txt = wd / "script.txt"
        script_txt.write_text("\n\n".join(s.text for s in segments), encoding="utf-8")
        srt = write_srt(segments, wd / "captions.srt")

        stage = "visuals"; rep.update(JobStatus.running, stage)
        size = frame_size(r["aspect_ratio"], r["resolution"])
        pngs = [render_slide(size, i, s.headline, s.text, r["subtitle_enabled"], wd / f"slide_{i:02d}.png")
                for i, s in enumerate(segments)]

        stage = "render"; rep.update(JobStatus.running, stage)
        clips = [render_clip(p, seg.duration, size, wd / f"clip_{i:02d}.mp4") for i, (p, seg) in enumerate(zip(pngs, segments))]
        video = concat_with_audio(clips, wd / "video.mp4", wd)

        stage = "thumbnail"; rep.update(JobStatus.running, stage)
        thumb = make_thumbnail(r["topic"], wd / "thumbnail.jpg") if r["thumbnail_enabled"] else None

        stage = "metadata"; rep.update(JobStatus.running, stage)
        meta = build_metadata(r, script_source, job_id)
        meta_path = wd / "metadata.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        stage = "quality_control"; rep.update(JobStatus.running, stage)
        qc = run_qc(video, size, float(r["duration_seconds"]), thumb, meta)
        if not qc["ok"]:
            failed = [f"{c['name']} ({c['detail']})" for c in qc["checks"] if not c["ok"]]
            raise StageError(stage, "QC thất bại, không upload: " + "; ".join(failed))

        notes = []
        if r["music_enabled"]:
            notes.append("music_enabled=true nhưng nhạc nền chưa được triển khai trong MVP")
        if r.get("voice_id"):
            notes.append("voice_id được bỏ qua: TTS chưa được triển khai trong MVP (audio là track im lặng)")
        notes.append("Phụ đề dùng thời gian ước lượng, chưa đồng bộ với giọng đọc")
        youtube = plan_youtube(r, env)

        report = {"job_id": job_id, "attempt": attempt, "script_source": script_source, "qc": qc,
                  "frame_size": list(size), "segments": len(segments), "youtube": youtube, "notes": notes}
        report_path = wd / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        files = {"video.mp4": video, "metadata.json": meta_path, "script.txt": script_txt,
                 "captions.srt": srt, "report.json": report_path}
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
            "files": up["files"], "youtube": youtube, "script_source": script_source,
            "qc": {"ok": True, **qc["probe"]}, "notes": notes,
        }
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
