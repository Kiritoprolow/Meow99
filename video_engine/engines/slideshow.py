"""Engine slideshow (pipeline MVP cũ): kịch bản -> slide Pillow -> FFmpeg. Logic được chuyển nguyên từ pipeline.py."""
from __future__ import annotations

from video_engine.engines.base import EngineOutput, RenderContext
from video_engine.renderer.ffmpeg_renderer import concat_with_audio, render_clip
from video_engine.script.generator import generate
from video_engine.subtitles.srt import write_srt
from video_engine.thumbnail.thumb import make_thumbnail
from video_engine.visuals.slides import render_slide


class SlideshowEngine:
    name = "slideshow"

    def render(self, ctx: RenderContext) -> EngineOutput:
        r, wd, size = ctx.request, ctx.workdir, ctx.size

        ctx.progress("script")
        segments, script_source = generate(r["topic"], r["language"], r["duration_seconds"], r["script"])
        script_txt = wd / "script.txt"
        script_txt.write_text("\n\n".join(s.text for s in segments), encoding="utf-8")
        srt = write_srt(segments, wd / "captions.srt")

        ctx.progress("visuals")
        pngs = [render_slide(size, i, s.headline, s.text, r["subtitle_enabled"], wd / f"slide_{i:02d}.png")
                for i, s in enumerate(segments)]

        ctx.progress("render")
        clips = [render_clip(p, seg.duration, size, wd / f"clip_{i:02d}.mp4") for i, (p, seg) in enumerate(zip(pngs, segments))]
        video = concat_with_audio(clips, wd / "video.mp4", wd)

        ctx.progress("thumbnail")
        thumb = make_thumbnail(r["topic"], wd / "thumbnail.jpg") if r["thumbnail_enabled"] else None

        notes = []
        if r["music_enabled"]:
            notes.append("music_enabled=true nhưng nhạc nền chưa được triển khai trong MVP")
        if r.get("voice_id"):
            notes.append("voice_id được bỏ qua: TTS chưa được triển khai trong MVP (audio là track im lặng)")
        notes.append("Phụ đề dùng thời gian ước lượng, chưa đồng bộ với giọng đọc")

        return EngineOutput(video=video, script_source=script_source, files={"script.txt": script_txt, "captions.srt": srt},
                            thumbnail=thumb, segments=len(segments), notes=notes)
