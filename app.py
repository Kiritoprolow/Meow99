"""Điểm vào cho Hugging Face Space (SDK gradio hoặc docker).

Space chỉ là gateway MCP — KHÔNG render video ở đây.
Endpoint MCP:  https://<space>.hf.space/<MCP_PATH_SECRET>/mcp   (ChatGPT, 'No authentication')
               https://<space>.hf.space/mcp  + header Authorization: Bearer <MCP_API_KEY>
"""
import os

import uvicorn

from app.gateway.asgi import build_app

app = build_app()

if __name__ == "__main__":
    # access_log=False: URL chứa secret, không được in vào log của Space.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")), access_log=False, log_level="warning")
