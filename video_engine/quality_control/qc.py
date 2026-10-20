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


def run_qc(video: Path, expected_size: tuple[int, int], expected_seconds: float, thumbnail: Path | None,
           metadata: dict[str, Any]) -> dict[str, Any]:
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
    add("audio_stream", a is not None, a.get("codec_name", "") if a else "")
    if v:
        got = (int(v.get("width", 0)), int(v.get("height", 0)))
        add("resolution", got == expected_size, f"{got[0]}x{got[1]} (mong đợi {expected_size[0]}x{expected_size[1]})")
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    tol = max(1.0, expected_seconds * 0.1)
    add("duration", abs(dur - expected_seconds) <= tol, f"{dur:.2f}s (mong đợi ~{expected_seconds}s ±{tol:.1f})")
    if thumbnail is not None:
        try:
            tw, th = check_thumbnail(thumbnail)
            add("thumbnail", tw > 0 and th > 0 and thumbnail.stat().st_size > 1000, f"{tw}x{th}")
        except Exception as exc:  # noqa: BLE001
            add("thumbnail", False, f"không mở được ảnh: {type(exc).__name__}")
    add("metadata", bool(metadata.get("title")) and isinstance(metadata.get("tags"), list), "")
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "probe": {"duration": round(dur, 3), "size_bytes": size,
                      "video_codec": v.get("codec_name") if v else None, "audio_codec": a.get("codec_name") if a else None}}
