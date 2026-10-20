"""Visual generator (MVP): nền gradient + chữ bằng Pillow, phụ đề burn-in thẳng vào ảnh.

Vẽ chữ bằng Pillow (không cần libass) -> hỗ trợ đầy đủ dấu tiếng Việt khi có font Unicode.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

RESOLUTIONS = {  # (cạnh ngắn) -> kích thước theo tỷ lệ; luôn là số chẵn cho H.264
    ("16:9", "480p"): (854, 480), ("16:9", "720p"): (1280, 720), ("16:9", "1080p"): (1920, 1080),
    ("9:16", "480p"): (480, 854), ("9:16", "720p"): (720, 1280), ("9:16", "1080p"): (1080, 1920),
    ("1:1", "480p"): (480, 480), ("1:1", "720p"): (720, 720), ("1:1", "1080p"): (1080, 1080),
}


def frame_size(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    return RESOLUTIONS[(aspect_ratio, resolution)]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError("Không tìm thấy font Unicode (cài gói fonts-dejavu-core)")


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def gradient(size: tuple[int, int], hue: float) -> Image.Image:
    w, h = size
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.65, 0.55))
    bot = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08) % 1, 0.75, 0.18))
    col = Image.new("RGB", (1, h))
    col.putdata([tuple(int(top[k] + (bot[k] - top[k]) * y / max(h - 1, 1)) for k in range(3)) for y in range(h)])
    return col.resize((w, h))


def _draw_block(d: ImageDraw.ImageDraw, lines: list[str], fnt, cx: int, y: int, fill, stroke: int, line_gap: float = 1.25) -> int:
    for ln in lines:
        d.text((cx, y), ln, font=fnt, fill=fill, anchor="mt", stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += int(fnt.size * line_gap)
    return y


def render_slide(size: tuple[int, int], index: int, headline: str, subtitle: str, show_subtitle: bool, out: Path) -> Path:
    w, h = size
    img = gradient(size, hue=(0.58 + 0.07 * index) % 1)
    d = ImageDraw.Draw(img)
    short = min(w, h)
    if headline:
        f = font(int(short * 0.085))
        lines = wrap(d, headline, f, int(w * 0.84))[:4]
        total = int(f.size * 1.25) * len(lines)
        _draw_block(d, lines, f, w // 2, int(h * 0.42) - total // 2, (255, 255, 255), max(2, short // 360))
    if show_subtitle and subtitle:
        f = font(int(short * 0.048))
        lines = wrap(d, subtitle, f, int(w * 0.88))[:5]
        total = int(f.size * 1.25) * len(lines)
        _draw_block(d, lines, f, w // 2, int(h * 0.88) - total, (255, 235, 130), max(2, short // 300))
    img.save(out, "PNG")
    return out
