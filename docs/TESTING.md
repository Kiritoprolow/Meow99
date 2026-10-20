# Kiểm thử: đã chạy gì, chưa chạy gì

Chạy: `python -m unittest discover -s tests -t .` (cần `mcp`, `pydantic`, `requests`, `uvicorn`, `pillow`, ffmpeg).
Kết quả lần chạy gần nhất khi phát triển: **54 test, tất cả đạt** (~25 giây).

## Đã kiểm chứng thật (trong môi trường phát triển)

| Nội dung | Loại | Cách kiểm chứng |
|---|---|---|
| Server MCP đúng giao thức, 7 tool, Streamable HTTP | Integration | uvicorn thật + client `mcp.Client` chính thức (mcp 2.3.0) gọi `list_tools`, `create_video_job`, `get_job_status` |
| Xác thực Bearer, secret-trong-URL, 401/404, giới hạn body (413) | Integration | HTTP thật |
| Entry point `app.py` + `scripts/check_gateway.py` | Smoke | chạy thật, log không chứa secret |
| Tạo job, validate, dispatch, ánh xạ job_id→run, trạng thái, reconcile, retry, cancel, giới hạn tần suất, che secret | Unit | GitHub **giả** (`tests/fakes.py`) + store trên đĩa |
| Render MP4 H.264+AAC (3 tỷ lệ), ffprobe, QC, thumbnail, SRT, tiếng Việt có dấu | Smoke | **FFmpeg/Pillow thật**; kiểm tra lại độc lập bằng ffprobe và xem khung hình |
| QC lỗi → không upload; lỗi → `failed` + secret bị che; hủy → `cancelled`; không ghi đè; chạy trùng là no-op; retry ra `attempt-2` | Unit/Integration | pipeline thật |
| `HubStorage`: một commit, ghim revision, xác minh kích thước, retry, từ chối ghi đè, private/public | Unit | module `huggingface_hub` **giả** |
| Workflow YAML hợp lệ cú pháp | Tĩnh | PyYAML |

## CHƯA kiểm chứng (cần tài khoản/dịch vụ thật)

- **GitHub Actions chưa từng chạy**: `render.yml` chưa được dispatch trên GitHub; `GitHubClient` chưa gọi API GitHub thật (chỉ giả lập). Kiểm tra: tạo repo, đặt secrets, gọi `create_video_job`.
- **Hugging Face Hub chưa từng được gọi thật**: `HubJobStore` (đọc qua `resolve/main`, liệt kê qua `/api/datasets/.../tree`) và `HubStorage` (`create_commit`, `get_paths_info`, `list_repo_tree`) mới chạy với giả lập. Có thể cần chỉnh nếu API thật khác (ví dụ độ trễ CDN khi đọc `status.json` ngay sau khi ghi, cách `list_repo_tree` báo thư mục không tồn tại).
- **Space chưa được deploy**; chưa thử `sdk: docker` hay `sdk: gradio` trên Hugging Face thật.
- **ChatGPT chưa được kết nối** với gateway này. Thông tin về ChatGPT (OAuth/No-auth, gói được dùng Developer Mode) lấy từ tài liệu OpenAI và một bài viết bên thứ ba; có thể đã thay đổi.
- **Upload YouTube**: chưa triển khai (Giai đoạn 5). **TTS, LLM, hình AI, nhạc**: chưa triển khai (Giai đoạn 4).
- Hiệu năng/giới hạn của runner miễn phí chưa đo.

Dữ liệu giả trong test (`hf_SECRETTOKEN…`, `ghp_SECRETTOKEN…`, `kkkk…`) chỉ là chuỗi thử nghiệm, không phải credential thật.
