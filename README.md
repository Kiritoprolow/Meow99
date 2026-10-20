# AI Video Studio

Điều khiển việc làm video bằng ngôn ngữ tự nhiên qua **ChatGPT → MCP → GitHub Actions → Hugging Face Hub**.

> **Trạng thái thật (đọc trước):** MVP đã render được MP4 thật và gateway MCP đã chạy thật trong môi trường phát triển, nhưng **chưa được thử trên GitHub Actions, Hugging Face Hub/Space và ChatGPT thật**. Giọng đọc (TTS), AI viết kịch bản, hình AI, nhạc nền và **đăng YouTube chưa được triển khai**. Chi tiết: [docs/TESTING.md](docs/TESTING.md).

## MVP hiện làm được gì

- 7 tool MCP: `create_video_job`, `get_job_status`, `list_jobs`, `get_job_result`, `retry_job`, `cancel_job`, `get_system_status`.
- Video mẫu thật: nền gradient + chữ + phụ đề cháy cứng (tiếng Việt có dấu), zoom nhẹ, H.264/AAC, 3 tỷ lệ (16:9, 9:16, 1:1), thumbnail JPEG, SRT, metadata, QC bằng ffprobe.
- **Âm thanh là track im lặng** (chưa có TTS). **Kịch bản** là script bạn gửi vào, hoặc kịch bản mẫu 3 câu từ chủ đề (không gọi AI). **Phụ đề** dùng thời gian ước lượng, chưa khớp giọng đọc.
- Mặc định an toàn: `preview` (không đăng YouTube), `private`.

## Kiến trúc (xem [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))

```
ChatGPT ──MCP/HTTPS──► Space (gateway, không render) ──workflow_dispatch──► GitHub Actions (render)
                              ▲                                                   │
                              └──── đọc status.json ◄── Hugging Face Hub (dataset) ◄── upload thành phẩm
```

## Tài khoản cần có

1. **GitHub** (chứa code + chạy Actions). 2. **Hugging Face** (Space làm gateway + dataset repo lưu dữ liệu). 3. **ChatGPT** gói có Developer Mode (xem mục kết nối). 4. (Sau này) Google Cloud cho YouTube.

## Cài đặt từng bước (làm được trên điện thoại)

### 1. GitHub
1. Tạo repo mới (public hoặc private), đẩy toàn bộ thư mục này lên (nhánh `main`). Cần có `.github/workflows/render.yml` trên nhánh mặc định.
2. Tạo **fine-grained personal access token**: chỉ repo này, quyền **Actions: Read and write**. Đây là `GITHUB_TOKEN` của gateway (đừng dùng token rộng hơn).
3. Repo → Settings → Secrets and variables → Actions:
   - Secret: `HF_TOKEN`
   - Variables: `HF_STATE_REPO`, `HF_OUTPUT_REPO` (ví dụ `tenban/video-studio-data`).

### 2. Hugging Face
1. Tạo **dataset repo** (nên **private**), ví dụ `tenban/video-studio-data`. Nếu chưa tạo, worker tự tạo bản private lúc upload đầu tiên.
2. Tạo **token** có quyền ghi vào repo đó → dùng làm `HF_TOKEN` (cho cả Actions và Space).
3. Tạo **Space**:
   - **Khuyến nghị: SDK Docker** — lấy `space/README.docker.md` làm `README.md` của Space, cùng `Dockerfile`, `app.py`, `requirements.txt`, thư mục `app/`.
   - Nếu muốn SDK Gradio: dùng `space/README.gradio.md` (sửa `sdk_version` thành phiên bản Gradio hiện hành; `app.py` chỉ chạy uvicorn trên cổng 7860, không dùng giao diện Gradio). Cách này **chưa được thử**.
4. Space → Settings → Variables and secrets, thêm các biến trong [.env.example](.env.example) (secret: `MCP_API_KEY`, `MCP_PATH_SECRET`, `GITHUB_TOKEN`, `HF_TOKEN`; biến thường: `GITHUB_REPO`, `HF_STATE_REPO`, `HF_OUTPUT_REPO`). Tạo chuỗi ngẫu nhiên: `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
5. Space chạy được khi mở `https://<space>.hf.space/healthz` thấy `{"status":"ok"}`. Thiếu cấu hình thì Space báo lỗi liệt kê biến còn thiếu (không in giá trị).

### 3. Kiểm tra gateway
```bash
pip install "mcp>=2.3,<3"
python scripts/check_gateway.py "https://<space>.hf.space/<MCP_PATH_SECRET>/mcp"
```
In ra danh sách tool và tình trạng GitHub/Hub. (Với client khác dùng URL `https://<space>.hf.space/mcp` + header `Authorization: Bearer <MCP_API_KEY>`.)

### 4. Kết nối ChatGPT
Theo tài liệu OpenAI, ChatGPT kết nối được server MCP từ xa qua HTTPS (Streamable HTTP hoặc SSE) với xác thực **OAuth hoặc không xác thực**; tài liệu **không** nhắc Bearer header. Một bài viết bên thứ ba cho biết Developer Mode dành cho Plus/Pro/Business/Enterprise/Education, **không có ở gói Free**, và các tool ghi (tạo/hủy/retry) sẽ hỏi xác nhận. Các thông tin này **chưa được tôi kiểm chứng trên tài khoản ChatGPT** và có thể đã đổi.

Cách làm: Settings → Connectors → Advanced → bật Developer Mode → Create → URL `https://<space>.hf.space/<MCP_PATH_SECRET>/mcp`, chọn **No authentication**.

**Điểm yếu cần biết:** vì ChatGPT không gửi Bearer, bảo mật dựa vào **secret nằm trong URL**. Ai có URL là gọi được tool (tạo job tốn quota GitHub). Giảm rủi ro: secret dài ngẫu nhiên, không chia sẻ/ảnh chụp URL, giới hạn `CREATE_LIMIT_PER_HOUR`, `MAX_DURATION_SECONDS`, xác nhận của ChatGPT cho tool ghi, và **xoay secret** khi nghi ngờ lộ. Gateway không ghi log đường dẫn (uvicorn chạy `access_log=False`). Muốn chắc hơn: cài OAuth cho gateway (chưa làm).

## Cách dùng

Nói với ChatGPT, ví dụ:
- "Tạo video 30 giây về vì sao bầu trời xanh, khung dọc 9:16, 720p." → ChatGPT gọi `create_video_job`, trả `job_id` ngay.
- "Kiểm tra job mới nhất." → `list_jobs` / `get_job_status` (chỉ có tên giai đoạn, không có % giả).
- "Lấy kết quả job_…" → `get_job_result`: URL video, thumbnail, metadata, trạng thái YouTube.
- "Chạy lại job đó" → `retry_job` (chỉ cho job failed/cancelled; thành phẩm vào `attempt-2`, không ghi đè bản cũ).

Video nằm ở `outputs/<job_id>/attempt-<n>/` trong dataset repo. **Nếu repo private, URL chỉ mở khi đăng nhập Hugging Face** (kết quả có `access: private`). Muốn link công khai, đặt repo public — cân nhắc kỹ vì mọi video sẽ công khai.

### YouTube (chưa triển khai upload)
`output_mode="publish"` hiện chỉ hoàn tất phần render + Hugging Face và báo `youtube.state = pending_configuration` (thiếu secret) hoặc `not_implemented` (đủ secret nhưng chưa có code upload). Sẽ không bao giờ báo "đã đăng" khi chưa đăng. Khi làm Giai đoạn 5 sẽ dùng OAuth 2.0 với `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` (đã khai báo sẵn trong workflow); đăng `public` luôn cần `confirm_public=true`.

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `dispatch_failed` / "thiếu quyền" | `GITHUB_TOKEN` sai repo hoặc thiếu Actions: Read and write; workflow chưa có trên nhánh `main`; sai `GITHUB_REPO`/`GITHUB_WORKFLOW`. |
| Job `queued` mãi | Run chưa bắt đầu hoặc Actions bị tắt/hết quota; xem tab Actions của repo. Gateway tự suy ra `failed` nếu run đã kết thúc mà worker không báo. |
| `failed` ở `uploading_to_hf` | `HF_TOKEN` thiếu quyền ghi hoặc sai tên repo; dùng `retry_job`. |
| `failed` ở `quality_control` | File render lỗi; xem `error`. Không upload khi QC hỏng. |
| Lần gọi đầu rất chậm / timeout | Space miễn phí có thể ngủ khi lâu không dùng; gọi lại sau vài chục giây (gateway không giữ session nên không mất gì). |
| 401 từ gateway | Sai secret/Bearer. 429: vượt giới hạn tần suất. 413: body quá lớn. |

## Giới hạn và chi phí

- Mọi thứ chạy trên gói miễn phí **có hạn mức** của GitHub Actions (phút chạy, dung lượng), Hugging Face (Space có thể ngủ, dung lượng dataset) và ChatGPT (gói trả phí). Tôi không ghi con số vì chúng thay đổi — kiểm tra trang giá/quota hiện hành của từng dịch vụ.
- Video lưu trên Hub là lưu trữ lâu dài; GitHub Artifacts **không** dùng làm kho chính.
- `MAX_DURATION_SECONDS` mặc định 120 để tránh đốt phút runner. Runner không có GPU; hình AI/video AI nặng cần provider ngoài (chưa làm).
- Hệ thống đơn người dùng: mọi lời gọi hợp lệ dùng chung một "chủ sở hữu". Giới hạn tần suất lưu trong RAM (mất khi Space restart; trạng thái job thì **không** mất vì nằm trên Hub).
- Liệt kê job đọc từng `status.json` nên chậm dần khi có hàng trăm job.

## Xoay vòng / thu hồi token

- `MCP_PATH_SECRET` / `MCP_API_KEY`: đổi giá trị trong Space → Restart; cập nhật URL trong ChatGPT.
- `GITHUB_TOKEN`: thu hồi ở GitHub → Settings → Developer settings; tạo token mới, cập nhật Space.
- `HF_TOKEN`: thu hồi/tạo lại ở Hugging Face → Settings → Access Tokens; cập nhật **cả** Space và GitHub Secrets.
- Nếu lỡ commit secret: coi như đã lộ, thu hồi ngay (xóa khỏi lịch sử git không đủ).

## Phát triển

```bash
python -m unittest discover -s tests -t .      # 54 test
STORE_BACKEND=local LOCAL_STORE_DIR=./.local_store python -m video_engine.run --job-id job_YYYYMMDD_HHMMSS_xxxxxx   # render cục bộ
```
Lộ trình: Giai đoạn 4 (TTS, LLM, provider adapter, nhạc/hình ngoài) → 5 (YouTube OAuth + chống đăng trùng) → thử trên dịch vụ thật.
