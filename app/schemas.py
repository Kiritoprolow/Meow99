"""Schema dữ liệu dùng chung cho gateway và video_engine."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_OPTIONS_BYTES = 4096


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    uploading_to_hf = "uploading_to_hf"
    uploading_to_youtube = "uploading_to_youtube"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


ACTIVE = {JobStatus.queued, JobStatus.running, JobStatus.uploading_to_hf, JobStatus.uploading_to_youtube}
TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


PROJECT_TYPES = ("video", "animation_3d")  # animation_2d / hybrid chưa triển khai -> bị từ chối rõ ràng, không giả vờ hỗ trợ
RENDER_PROFILES = ("preview", "standard")
# Giới hạn fail-fast cho runner miễn phí: số frame tối đa theo profile (và 1080p bị giảm một nửa)
MAX_3D_FRAMES = {"preview": 240, "standard": 480}
MAX_3D_FRAMES_1080P = 240

# Ước lượng thời gian render để fail-fast TRƯỚC khi tốn một lượt GitHub Actions (runner miễn phí: 4 vCPU, không GPU).
# Số đo thật (giây/frame ở 854x480, 4 core): cycles 12spp ~1.5 | cycles 32spp+denoise ~5.7 | eevee 4spp ~2.6 | eevee 16spp ~8-10.
# Bảng dưới đã nhân hệ số an toàn ~2 vì runner Azure đo chậm hơn máy thử khoảng 2 lần; hiệu chỉnh lại khi có thêm số đo.
SECONDS_PER_FRAME_480P = {("cycles", "preview"): 3.0, ("cycles", "standard"): 11.0,
                          ("eevee", "preview"): 4.0, ("eevee", "standard"): 12.5}
RENDER_FIXED_OVERHEAD_SECONDS = 40.0   # khởi động Blender + frame đầu (biên dịch shader)
RENDER_BUDGET_SECONDS = 1000.0         # < BLENDER_RENDER_TIMEOUT mặc định (1200s) để còn dư địa
_PIXELS = {("16:9", "480p"): 854 * 480, ("16:9", "720p"): 1280 * 720, ("16:9", "1080p"): 1920 * 1080,
           ("9:16", "480p"): 480 * 854, ("9:16", "720p"): 720 * 1280, ("9:16", "1080p"): 1080 * 1920,
           ("1:1", "480p"): 480 * 480, ("1:1", "720p"): 720 * 720, ("1:1", "1080p"): 1080 * 1080}


def estimate_render_seconds(engine: str, profile: str, aspect_ratio: str, resolution: str, frames: int) -> float:
    per_frame = SECONDS_PER_FRAME_480P[(engine, profile)] * _PIXELS[(aspect_ratio, resolution)] / _PIXELS[("16:9", "480p")]
    return RENDER_FIXED_OVERHEAD_SECONDS + per_frame * frames


class Scene3DOptions(BaseModel):
    """Tham số cảnh cho project_type=animation_3d, nằm trong `options`. extra=forbid để lỗi chính tả bị báo ngay."""

    model_config = ConfigDict(extra="forbid")

    shape: Literal["cube", "sphere", "torus", "cone"] = "torus"
    object_color: str = Field(default="#e4572e", pattern=r"^#[0-9a-fA-F]{6}$")
    background_color: str = Field(default="#1b2a41", pattern=r"^#[0-9a-fA-F]{6}$")
    camera_move: Literal["orbit", "dolly"] = "orbit"
    fps: int = Field(default=24, ge=12, le=30)
    engine: Literal["eevee", "cycles"] = "cycles"  # trên runner không GPU, Cycles CPU nhanh hơn EEVEE (OpenGL phần mềm)


class VideoJobRequest(BaseModel):
    """Payload tạo job. Trường tùy chọn có mặc định hợp lý."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=500, description="Chủ đề video")
    style: str = Field(default="explainer", max_length=60)
    duration_seconds: int = Field(default=30, ge=5, le=600)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    resolution: Literal["480p", "720p", "1080p"] = "720p"
    language: str = Field(default="vi", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    target_audience: str = Field(default="", max_length=200)
    voice_id: str | None = Field(default=None, max_length=80)
    script: str | None = Field(default=None, max_length=8000)
    visual_style: str = Field(default="gradient", max_length=60)
    subtitle_enabled: bool = True
    music_enabled: bool = False
    thumbnail_enabled: bool = True
    output_mode: Literal["preview", "publish"] = "preview"
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    confirm_public: bool = Field(default=False, description="Bắt buộc true nếu youtube_privacy=public")
    project_type: Literal["video", "animation_3d"] = Field(
        default="video", description="video = slideshow cũ; animation_3d = cảnh Blender 3D thật")
    render_profile: Literal["preview", "standard"] = Field(
        default="preview", description="Chỉ áp dụng cho animation_3d: preview (nhanh, mẫu thấp) hoặc standard")
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def _options_small(cls, v: dict[str, Any]) -> dict[str, Any]:
        import json

        try:
            size = len(json.dumps(v, ensure_ascii=False).encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("options phải là JSON hợp lệ") from exc
        if size > MAX_OPTIONS_BYTES:
            raise ValueError(f"options quá lớn (>{MAX_OPTIONS_BYTES} bytes)")
        return v

    @model_validator(mode="after")
    def _public_needs_confirm(self) -> "VideoJobRequest":
        if self.output_mode == "publish" and self.youtube_privacy == "public" and not self.confirm_public:
            raise ValueError("Đăng công khai cần confirm_public=true (xác nhận rõ ràng)")
        return self

    @model_validator(mode="after")
    def _project_type_rules(self) -> "VideoJobRequest":
        if self.project_type == "video":
            if self.render_profile != "preview":
                raise ValueError("render_profile chỉ áp dụng cho project_type=animation_3d")
            return self
        # animation_3d: kiểm tra sớm (trước khi dispatch) để fail-fast, không tốn lượt chạy GitHub Actions
        try:
            opts = Scene3DOptions.model_validate(self.options)
        except ValueError as exc:
            raise ValueError(f"options không hợp lệ cho animation_3d: {exc}") from None
        frames = self.duration_seconds * opts.fps
        cap = MAX_3D_FRAMES[self.render_profile]
        if self.resolution == "1080p":
            if self.render_profile != "standard":
                raise ValueError("1080p cần render_profile=standard")
            cap = min(cap, MAX_3D_FRAMES_1080P)
        if frames > cap:
            raise ValueError(
                f"animation_3d/{self.render_profile}{'/1080p' if self.resolution == '1080p' else ''}: tối đa {cap} frame "
                f"(duration_seconds x fps = {frames}); giảm duration_seconds hoặc fps")
        est = estimate_render_seconds(opts.engine, self.render_profile, self.aspect_ratio, self.resolution, frames)
        if est > RENDER_BUDGET_SECONDS:
            raise ValueError(
                f"Ước lượng render ~{est:.0f}s vượt ngân sách {RENDER_BUDGET_SECONDS:.0f}s của runner miễn phí "
                f"({opts.engine}/{self.render_profile}/{self.resolution}, {frames} frame); giảm resolution, fps, duration_seconds "
                f"hoặc dùng render_profile=preview")
        return self


class JobRecord(BaseModel):
    """Nội dung status.json — chỉ worker (và gateway lúc tạo) ghi."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: JobStatus = JobStatus.queued
    stage: str = "queued"
    attempt: int = 1
    owner: str = "default"
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)
    run_id: int | None = None
    run_url: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"owner"})
