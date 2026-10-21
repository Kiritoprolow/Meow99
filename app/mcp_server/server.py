"""MCP server (SDK `mcp` 2.x, lớp MCPServer, transport Streamable HTTP).

7 tool theo đặc tả. Mọi tool đều trả về dict có khóa `ok`; lỗi nghiệp vụ -> {"ok": False, "error": {...}}.
Không có tool nào nhận mã/lệnh tùy ý.
"""
from __future__ import annotations

from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
    MCP_V2 = True
except ImportError:  # mcp 1.x: Space Gradio luôn cài gradio[mcp] (yêu cầu mcp<2)
    from mcp.server.fastmcp import FastMCP as MCPServer
    MCP_V2 = False

from app.service import JobService, ServiceError

INSTRUCTIONS = (
    "AI Video Studio: tạo video bất đồng bộ. Gọi create_video_job để nhận job_id (không chờ render), "
    "sau đó gọi get_job_status định kỳ và get_job_result khi status=completed. "
    "Mặc định output_mode='preview' (không đăng YouTube) và youtube_privacy='private'. "
    "Chỉ dùng output_mode='publish' khi người dùng nói rõ muốn đăng; đăng 'public' cần confirm_public=true."
)


def build_mcp(service: JobService, owner: str = "default", **server_kwargs: Any) -> Any:
    """server_kwargs (cấu hình HTTP) chỉ dùng với mcp 1.x; mcp 2.x nhận chúng ở streamable_http_app()."""
    mcp = MCPServer(name="ai-video-studio", instructions=INSTRUCTIONS, **({} if MCP_V2 else server_kwargs))

    def guard(fn):  # chuyển ServiceError thành kết quả có cấu trúc
        def run(*a: Any, **kw: Any) -> dict[str, Any]:
            try:
                return fn(*a, **kw)
            except ServiceError as exc:
                return exc.as_dict()
        return run

    @mcp.tool()
    def create_video_job(
        topic: str,
        style: str = "explainer",
        duration_seconds: int = 30,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        language: str = "vi",
        target_audience: str = "",
        voice_id: str | None = None,
        script: str | None = None,
        visual_style: str = "gradient",
        subtitle_enabled: bool = True,
        music_enabled: bool = False,
        thumbnail_enabled: bool = True,
        output_mode: str = "preview",
        youtube_privacy: str = "private",
        confirm_public: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Tạo job sản xuất video (bất đồng bộ). Trả về job_id ngay, không chờ render.

        aspect_ratio: 16:9 | 9:16 | 1:1. resolution: 480p | 720p | 1080p.
        output_mode: preview (chỉ lưu lên Hugging Face Hub) | publish (thêm đăng YouTube nếu đã cấu hình).
        youtube_privacy: private | unlisted | public (public cần confirm_public=true).
        script: kịch bản có sẵn; nếu bỏ trống, hệ thống dùng kịch bản mẫu từ topic (không gọi LLM).
        """
        payload = dict(topic=topic, style=style, duration_seconds=duration_seconds, aspect_ratio=aspect_ratio,
                       resolution=resolution, language=language, target_audience=target_audience,
                       voice_id=voice_id, script=script, visual_style=visual_style,
                       subtitle_enabled=subtitle_enabled, music_enabled=music_enabled,
                       thumbnail_enabled=thumbnail_enabled, output_mode=output_mode,
                       youtube_privacy=youtube_privacy, confirm_public=confirm_public, options=options or {})
        return guard(service.create_job)(payload, owner)

    @mcp.tool()
    def get_job_status(job_id: str) -> dict[str, Any]:
        """Xem trạng thái job: status, stage (giai đoạn), thời điểm cập nhật, lỗi, kết quả nếu đã xong."""
        return guard(service.get_job_status)(job_id, owner)

    @mcp.tool()
    def list_jobs(limit: int = 10, status: str | None = None) -> dict[str, Any]:
        """Liệt kê job gần đây (tối đa 30). status: queued|running|uploading_to_hf|uploading_to_youtube|completed|failed|cancelled."""
        return guard(service.list_jobs)(limit, status, owner)

    @mcp.tool()
    def get_job_result(job_id: str) -> dict[str, Any]:
        """Lấy kết quả cuối: URL video/thumbnail/metadata trên Hugging Face Hub, trạng thái YouTube, QC."""
        return guard(service.get_job_result)(job_id, owner)

    @mcp.tool()
    def retry_job(job_id: str) -> dict[str, Any]:
        """Chạy lại job failed/cancelled. Từ chối nếu job đã đăng YouTube (tránh đăng trùng)."""
        return guard(service.retry_job)(job_id, owner)

    @mcp.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Hủy job đang chạy. Chỉ báo cancelled=true khi GitHub xác nhận run đã hủy."""
        return guard(service.cancel_job)(job_id, owner)

    @mcp.tool()
    def get_system_status() -> dict[str, Any]:
        """Kiểm tra gateway, GitHub API, job store, Hugging Face Hub. Không hiển thị secret."""
        return guard(service.system_status)()

    return mcp
