"""Quality control bằng ffprobe + Pillow. Trả về danh sách kiểm tra; ok=False nếu có kiểm tra bắt buộc thất bại."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from video_engine.thumbnail.thumb import check_thumbnail

MIN_BYTES = 5_000


def ffprobe(path: Path) -> dict[str, Any]:
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("ffprobe không đọc được file: " + p.stderr.strip()[-200:])
    return json.loads(p.stdout)


MOTION_SAMPLE_FPS = 6          # lấy mẫu 6 khung/giây để so sánh
MOTION_WIDTH = 96              # thu nhỏ + xám để so sánh nhanh, ít nhạy nhiễu nén
MOTION_MAX_SAMPLES = 240
MOTION_STEP_THRESHOLD = 0.15   # độ lệch điểm ảnh trung bình (0-255) giữa 2 mẫu liên tiếp coi là "có thay đổi"
MOTION_MIN_CHANGED_RATIO = 0.6
MOTION_MIN_FIRST_LAST = 1.0


def motion_stats(video: Path, width: int, height: int) -> dict[str, Any]:
    """Giải mã mẫu khung hình bằng ffmpeg và đo mức thay đổi theo thời gian (không cần numpy).

    Video tĩnh (hoặc chỉ đổi 1 lần) cho changed_ratio ~ 0 nên bị loại; video có chuyển động liên tục cho ~ 1.
    """
    mw = MOTION_WIDTH
    mh = max(2, int(round(mw * height / max(width, 1))))
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={MOTION_SAMPLE_FPS},scale={mw}:{mh},format=gray",
                        "-frames:v", str(MOTION_MAX_SAMPLES), "-f", "rawvideo", "-"],
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg không giải mã được video: " + p.stderr.decode(errors="replace").strip()[-200:])
    fsz = mw * mh
    frames = [p.stdout[i:i + fsz] for i in range(0, len(p.stdout) - fsz + 1, fsz)]
    steps = [sum(abs(a - b) for a, b in zip(f1, f2)) / fsz for f1, f2 in zip(frames, frames[1:])]
    changed = sum(1 for d in steps if d >= MOTION_STEP_THRESHOLD)
    first_last = sum(abs(a - b) for a, b in zip(frames[0], frames[-1])) / fsz if len(frames) >= 2 else 0.0
    return {"samples": len(frames), "changed_ratio": round(changed / len(steps), 3) if steps else 0.0,
            "mean_step_diff": round(sum(steps) / len(steps), 3) if steps else 0.0,
            "first_last_diff": round(first_last, 3)}


def run_qc(video: Path, expected_size: tuple[int, int], expected_seconds: float, thumbnail: Path | None,
           metadata: dict[str, Any], *, require_audio: bool = True, check_motion: bool = False,
           expected_frames: int | None = None, expected_fps: float | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    exists = video.exists()
    add("video_exists", exists)
    size = video.stat().st_size if exists else 0
    add("video_size", size >= MIN_BYTES, f"{size} bytes")
    info: dict[str, Any] = {}
    if exists and size:
        try:
            info = ffprobe(video)
        except Exception as exc:  # noqa: BLE001
            add("ffprobe_readable", False, str(exc))
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    add("video_stream", v is not None, v.get("codec_name", "") if v else "")
    if require_audio:
        add("audio_stream", a is not None, a.get("codec_name", "") if a else "")
    else:  # animation_3d: không có audio là đúng thiết kế; nếu lại có audio thì là bất thường vì pipeline không tạo
        add("no_audio_stream", a is None, "video không có audio (đúng thiết kế)" if a is None else "có audio ngoài dự kiến")
    if v:
        got = (int(v.get("width", 0)), int(v.get("height", 0)))
        add("resolution", got == expected_size, f"{got[0]}x{got[1]} (mong đợi {expected_size[0]}x{expected_size[1]})")
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    tol = max(1.0, expected_seconds * 0.1)
    add("duration", abs(dur - expected_seconds) <= tol, f"{dur:.2f}s (mong đợi ~{expected_seconds}s ±{tol:.1f})")
    if v and expected_fps is not None:
        num, _, den = str(v.get("avg_frame_rate", "0/1")).partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        add("frame_rate", abs(fps - expected_fps) <= 0.05, f"{fps:.3f} fps (mong đợi {expected_fps})")
    if v and expected_frames is not None:
        nb = v.get("nb_frames")
        if nb is None:  # mp4 thường có nb_frames; nếu thiếu thì đếm thật bằng ffprobe
            q = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                "stream=nb_read_frames", "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=120)
            nb = q.stdout.strip() or None
        add("frame_count", str(nb).isdigit() and int(nb) == expected_frames, f"{nb} frame (mong đợi {expected_frames})")
    if v and check_motion:
        try:
            m = motion_stats(video, int(v.get("width", 0)), int(v.get("height", 0)))
            ok = (m["samples"] >= 3 and m["changed_ratio"] >= MOTION_MIN_CHANGED_RATIO
                  and m["first_last_diff"] >= MOTION_MIN_FIRST_LAST)
            add("motion", ok, f"{m['samples']} mẫu, {m['changed_ratio']:.0%} bước có thay đổi, "
                              f"lệch đầu-cuối {m['first_last_diff']}")
            info["_motion"] = m
        except Exception as exc:  # noqa: BLE001
            add("motion", False, str(exc))
    if thumbnail is not None:
        try:
            tw, th = check_thumbnail(thumbnail)
            add("thumbnail", tw > 0 and th > 0 and thumbnail.stat().st_size > 1000, f"{tw}x{th}")
        except Exception as exc:  # noqa: BLE001
            add("thumbnail", False, f"không mở được ảnh: {type(exc).__name__}")
    add("metadata", bool(metadata.get("title")) and isinstance(metadata.get("tags"), list), "")
    probe = {"duration": round(dur, 3), "size_bytes": size,
             "video_codec": v.get("codec_name") if v else None, "audio_codec": a.get("codec_name") if a else None}
    if "_motion" in info:
        probe["motion"] = info["_motion"]
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "probe": probe}
