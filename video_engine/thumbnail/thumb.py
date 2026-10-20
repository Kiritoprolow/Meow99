"""Thumbnail 16:9 (1280x720) JPEG: chữ lớn, đọc được trên điện thoại."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from video_engine.visuals.slides import font, gradient, wrap

THUMB_SIZE = (1280, 720)


def make_thumbnail(title: str, out: Path) -> Path:
    img = gradient(THUMB_SIZE, hue=0.58)
    d = ImageDraw.Draw(img)
    f = font(92)
    lines = wrap(d, title, f, int(THUMB_SIZE[0] * 0.86))[:4]
    step = int(f.size * 1.2)
    y = THUMB_SIZE[1] // 2 - step * len(lines) // 2
    for ln in lines:
        d.text((THUMB_SIZE[0] // 2, y), ln, font=f, fill=(255, 255, 255), anchor="mt", stroke_width=5, stroke_fill=(0, 0, 0))
        y += step
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out


def check_thumbnail(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        return im.size
