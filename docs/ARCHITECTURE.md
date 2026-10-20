# Kiến trúc, luồng dữ liệu, schema

## Ai chạy ở đâu

| Thành phần | Chạy ở | Việc làm | KHÔNG làm |
|---|---|---|---|
| ChatGPT | OpenAI | hiểu yêu cầu, gọi tool MCP, đọc kết quả | — |
| Gateway (`app/`, `app.py`) | Hugging Face Space | xác thực, validate, dispatch GitHub Actions, đọc trạng thái | render, xử lý video, giữ request mở |
| Engine (`video_engine/`) | GitHub Actions runner | script → hình → FFmpeg → QC → upload | — |
| Job store + thành phẩm | Hugging Face Hub (dataset repo) | `jobs/<id>/…` và `outputs/<id>/attempt-<n>/…` | — |

## Luồng

```
ChatGPT ──MCP (Streamable HTTP, HTTPS)──► Gateway (Space)
                                           │ 1. validate payload
                                           │ 2. ghi jobs/<id>/request.json + status.json(queued) lên Hub
                                           │ 3. POST workflow_dispatch {job_id, attempt}  → trả job_id NGAY
                                           ▼
                                   GitHub Actions: video-job <job_id> #<attempt>
                                           │ đọc request.json (Hub)
                                           │ script → slides → FFmpeg → thumbnail → metadata → QC
                                           │ ghi status.json mỗi giai đoạn (kèm run_id)
                                           │ upload 1 commit: outputs/<id>/attempt-<n>/*  → verify
                                           │ status.json = completed + result
                                           ▼
ChatGPT ◄── get_job_status / get_job_result ◄── Gateway đọc status.json (+ đối chiếu GitHub run)
```

## Ánh xạ job_id ↔ workflow run

1. Workflow có `run-name: video-job <job_id> #<attempt>` → gateway tìm run theo tên (`find_run`).
2. Worker ghi `run_id` vào `status.json` ngay khi bắt đầu (nguồn chính xác hơn, dùng ưu tiên).
3. **Đối chiếu (reconcile):** nếu run đã `completed` mà `status.json` vẫn đang chạy (worker chết, bị kill, lỗi cài đặt) → gateway suy ra `failed`/`cancelled` thay vì báo "running" mãi.
4. `cancel_job` chỉ báo `cancelled: true` khi GitHub xác nhận run `completed/cancelled`; nếu chưa, trả `cancel_requested`.

## Phân quyền ghi (tránh ghi đè lẫn nhau)

- `request.json`: gateway ghi **một lần** (tạo mới, từ chối nếu đã có).
- `status.json`: gateway ghi lúc tạo/retry/xác nhận hủy; worker ghi các giai đoạn còn lại.
- Thành phẩm: thư mục `attempt-<n>` riêng; từ chối nếu đã tồn tại; chạy trùng một attempt đã `completed` là no-op.

## Payload `create_video_job`

| Trường | Kiểu / giá trị | Mặc định |
|---|---|---|
| `topic` | chuỗi 1–500 | bắt buộc |
| `style`, `visual_style`, `target_audience` | chuỗi ngắn | `explainer`, `gradient`, `""` |
| `duration_seconds` | 5–600 (gateway giới hạn `MAX_DURATION_SECONDS`, mặc định 120) | 30 |
| `aspect_ratio` | `16:9` `9:16` `1:1` | `16:9` |
| `resolution` | `480p` `720p` `1080p` | `720p` |
| `language` | mã ngôn ngữ (`vi`, `en`, `en-US`) | `vi` |
| `voice_id`, `script` | tùy chọn (script ≤ 8000 ký tự) | `null` |
| `subtitle_enabled`, `thumbnail_enabled` | bool | `true` |
| `music_enabled` | bool (MVP: chưa có nhạc, được ghi vào `notes`) | `false` |
| `output_mode` | `preview` \| `publish` | `preview` |
| `youtube_privacy` | `private` `unlisted` `public` | `private` |
| `confirm_public` | bắt buộc `true` nếu `publish` + `public` | `false` |
| `options` | object ≤ 4 KB | `{}` |

Trường lạ bị từ chối (`extra=forbid`).

## Kết quả (`result` trong `status.json` / `get_job_result`)

```json
{
  "repo_id": "user/video-studio-data", "revision": "<commit oid>", "access": "private|public",
  "prefix": "outputs/<job_id>/attempt-1",
  "video_url": "…/resolve/<revision>/outputs/<job_id>/attempt-1/video.mp4",
  "thumbnail_url": "…", "metadata_url": "…", "captions_url": "…", "script_url": "…", "report_url": "…",
  "files": [{"path": "…", "size": 123}],
  "youtube": {"state": "skipped|pending_configuration|not_implemented"},
  "script_source": "user|template", "qc": {"ok": true, "duration": 12.0, "size_bytes": 257281,
  "video_codec": "h264", "audio_codec": "aac"}, "notes": ["…"]
}
```

URL được ghim vào `revision` (commit) nên không đổi nội dung về sau. Nếu `access = private`, URL chỉ mở được khi đăng nhập Hugging Face có quyền.

## Trạng thái

`queued → running → uploading_to_hf → (uploading_to_youtube, Giai đoạn 5) → completed`, hoặc `failed` / `cancelled`.
`stage` là tên giai đoạn (`script`, `visuals`, `render`, `thumbnail`, `metadata`, `quality_control`, …). **Không có phần trăm tiến độ** vì không đo được đáng tin cậy.
