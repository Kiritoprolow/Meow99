"""Script generator (MVP): dùng script người dùng cung cấp, hoặc kịch bản MẪU từ topic.

KHÔNG gọi LLM. Báo cáo ghi rõ script_source = "user" | "template" để không giả vờ có AI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SEGMENTS = 12
MIN_SEG_SECONDS = 1.5


@dataclass
class Segment:
    headline: str   # chữ lớn trên màn hình
    text: str       # lời dẫn / phụ đề
    start: float = 0.0
    end: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


_TEMPLATES = {
    "vi": ("Hôm nay chúng ta cùng tìm hiểu: {t}.", "Đây là nội dung mẫu được tạo tự động cho chủ đề này.",
           "Cảm ơn bạn đã xem. Hãy cho biết bạn muốn tìm hiểu thêm điều gì."),
    "en": ("Today we explore: {t}.", "This is auto-generated sample content for this topic.",
           "Thanks for watching. Tell us what you want to learn next."),
}


def _split_user_script(script: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", script.strip()) if p.strip()]
    if len(parts) < 2:  # không có đoạn trống -> tách theo câu
        parts = [p.strip() for p in re.split(r"(?<=[.!?…。])\s+", script.strip()) if p.strip()]
    parts = [re.sub(r"\s+", " ", p) for p in parts]
    while len(parts) > MAX_SEGMENTS:  # gộp các đoạn cuối cho đủ giới hạn
        parts[-2:] = [parts[-2] + " " + parts[-1]]
    return parts


def _allocate(texts: list[str], total: float) -> list[tuple[float, float]]:
    weights = [max(len(t), 1) for t in texts]
    raw = [total * w / sum(weights) for w in weights]
    # đảm bảo tối thiểu, rồi chuẩn hóa lại để tổng đúng bằng total
    raw = [max(r, MIN_SEG_SECONDS) for r in raw]
    scale = total / sum(raw)
    out, t = [], 0.0
    for r in raw:
        d = r * scale
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    out[-1] = (out[-1][0], round(total, 3))
    return out


def generate(topic: str, language: str, duration_seconds: int, script: str | None) -> tuple[list[Segment], str]:
    if script and script.strip():
        texts, source = _split_user_script(script), "user"
        heads = [topic] + [""] * (len(texts) - 1)
    else:
        tpl = _TEMPLATES.get(language.split("-")[0], _TEMPLATES["en"])
        topic_clean = topic.strip()
        first = tpl[0].format(t=topic_clean)
        if topic_clean[-1:] in ".!?…":  # tránh dấu câu kép ("xanh?.")
            first = first[:-1]
        texts, source = [first, tpl[1], tpl[2]], "template"
        heads = [topic, "", ""]
    spans = _allocate(texts, float(duration_seconds))
    return [Segment(h, t, s, e) for h, t, (s, e) in zip(heads, texts, spans)], source
