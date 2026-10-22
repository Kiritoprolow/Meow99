"""Giao diện chung cho các engine dựng video (slideshow cũ, Blender 3D, ...).

Engine chỉ làm phần SÁNG TẠO NỘI DUNG: nhận RenderContext, trả EngineOutput (video + file phụ).
Phần dùng chung (metadata, QC, upload, trạng thái job) vẫn nằm ở video_engine.pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class StageError(RuntimeError):
    """Lỗi gắn với một giai đoạn cụ thể; pipeline ghi `stage` này vào status.json."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class RenderContext:
    job_id: str
    attempt: int
    request: dict[str, Any]          # VideoJobRequest.model_dump() đã được validate
    workdir: Path                    # thư mục tạm RIÊNG của job+attempt này
    size: tuple[int, int]            # (rộng, cao) theo aspect_ratio + resolution
    progress: Callable[[str], None]  # báo giai đoạn hiện tại ra status.json
    env: Mapping[str, str]


@dataclass
class EngineOutput:
    video: Path
    script_source: str                                   # template | user | procedural ...
    files: dict[str, Path] = field(default_factory=dict)  # artifact phụ: tên trên Hub -> đường dẫn
    thumbnail: Path | None = None
    segments: int | None = None
    notes: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)  # chi tiết riêng của engine -> report.json
    metadata_overrides: dict[str, Any] = field(default_factory=dict)  # giá trị None = xóa khóa khỏi metadata
    qc_options: dict[str, Any] = field(default_factory=dict)  # tham số bổ sung cho run_qc


class RenderEngine(Protocol):
    name: str

    def render(self, ctx: RenderContext) -> EngineOutput: ...
