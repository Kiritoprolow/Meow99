"""Xuất SRT. Thời gian là ƯỚC LƯỢNG theo độ dài chữ (MVP chưa có giọng đọc để đồng bộ thật)."""
from __future__ import annotations

from pathlib import Path

from video_engine.script.generator import Segment


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[Segment], path: Path) -> Path:
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path
