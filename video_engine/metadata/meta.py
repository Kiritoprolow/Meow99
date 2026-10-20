"""Metadata (MVP): title/description/tags từ topic bằng quy tắc đơn giản, không nhồi từ khóa."""
from __future__ import annotations

import re
from typing import Any


def build_metadata(req: dict[str, Any], script_source: str, job_id: str) -> dict[str, Any]:
    topic = req["topic"].strip()
    title = topic if len(topic) <= 90 else topic[:87].rstrip() + "..."
    words = [w.lower() for w in re.findall(r"\w+", topic, flags=re.UNICODE) if len(w) > 2]
    tags = list(dict.fromkeys(words))[:8]
    desc = topic
    if script_source == "template":
        desc += "\n\n(Video mẫu được tạo tự động, nội dung chưa được kiểm chứng.)"
    return {
        "job_id": job_id,
        "title": title,
        "description": desc,
        "tags": tags,
        "language": req.get("language", "vi"),
        "style": req.get("style"),
        "script_source": script_source,
        "audio": "silent_placeholder",  # MVP chưa có TTS
        "subtitle_timing": "estimated",  # chưa đồng bộ với giọng đọc
        "assets": {"visuals": "generated_locally_gradient", "music": None, "licenses": "no third-party assets"},
    }
