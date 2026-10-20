"""Renderer FFmpeg: ảnh tĩnh + zoom nhẹ -> clip, nối clip, ghép track audio, xuất MP4 H.264/AAC."""
from __future__ import annotations

import subprocess
from pathlib import Path

FPS = 25


class RenderError(RuntimeError):
    pass


def run(cmd: list[str], timeout: int = 600) -> None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RenderError(f"{cmd[0]} quá thời gian {timeout}s") from None
    except FileNotFoundError:
        raise RenderError(f"Không tìm thấy {cmd[0]} (cài ffmpeg)") from None
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-8:])
        raise RenderError(f"{cmd[0]} lỗi (mã {p.returncode}):\n{tail}")


def render_clip(png: Path, seconds: float, size: tuple[int, int], out: Path) -> Path:
    w, h = size
    frames = max(int(round(seconds * FPS)), 1)
    # phóng 2x trước zoompan để chuyển động mượt, không giật pixel
    vf = (f"scale={w * 2}:{h * 2},"
          f"zoompan=z='min(1+0.0006*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          "format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-vf", vf,
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-r", str(FPS), str(out)])
    return out


def concat_with_audio(clips: list[Path], out: Path, workdir: Path) -> Path:
    """Nối clip, thêm track audio im lặng (MVP chưa có TTS) để file có đủ audio stream."""
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest",
         "-movflags", "+faststart", str(out)])
    return out
