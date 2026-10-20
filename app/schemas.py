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
