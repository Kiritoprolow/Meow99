"""YouTube (Giai đoạn 5 CHƯA triển khai upload). Hiện chỉ quyết định trạng thái theo chế độ + cấu hình.

Không bao giờ trả state="uploaded" khi chưa upload thật.
"""
from __future__ import annotations

from typing import Any, Mapping

REQUIRED = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")


def plan_youtube(request: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    if request.get("output_mode") != "publish":
        return {"state": "skipped", "detail": "output_mode=preview, không đăng YouTube"}
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        return {"state": "pending_configuration", "detail": "Thiếu secret: " + ", ".join(missing),
                "privacy": request.get("youtube_privacy", "private")}
    return {"state": "not_implemented", "detail": "Đã có đủ secret nhưng upload YouTube chưa được triển khai (Giai đoạn 5).",
            "privacy": request.get("youtube_privacy", "private")}
