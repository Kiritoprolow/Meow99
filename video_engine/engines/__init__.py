"""Đăng ký engine theo project_type. Blender được import trễ: job video thường không đụng tới code Blender."""
from __future__ import annotations

from video_engine.engines.base import EngineOutput, RenderContext, RenderEngine, StageError


def get_engine(project_type: str) -> RenderEngine:
    if project_type == "video":
        from video_engine.engines.slideshow import SlideshowEngine
        return SlideshowEngine()
    if project_type == "animation_3d":
        from video_engine.engines.blender_3d import Blender3DEngine
        return Blender3DEngine()
    raise StageError("loading_request", f"project_type chưa được hỗ trợ: {project_type}")


__all__ = ["EngineOutput", "RenderContext", "RenderEngine", "StageError", "get_engine"]
