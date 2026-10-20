"""Integration test: uvicorn thật + client MCP chính thức qua Streamable HTTP.

Kiểm chứng: endpoint đúng giao thức MCP, danh sách 7 tool, gọi tool tạo job thật (GitHub giả),
xác thực Bearer / secret-trong-URL, từ chối sai secret, giới hạn body.
"""
import asyncio
import socket
import threading
import time
import unittest

import requests
import uvicorn
from mcp import Client

from app.gateway.asgi import build_app
from tests.fakes import API_KEY, PATH_SECRET, make_service

EXPECTED_TOOLS = {"create_video_job", "get_job_status", "list_jobs", "get_job_result",
                  "retry_job", "cancel_job", "get_system_status"}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GatewayE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc, cls.gh, cls.store = make_service(max_body_bytes=8192)
        cls.port = free_port()
        app = build_app(cls.svc.s, cls.svc)
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error", access_log=False))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.1)
        assert cls.server.started, "uvicorn không khởi động được"
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def test_healthz_public_and_minimal(self):
        r = requests.get(f"{self.base}/healthz", timeout=5)
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_mcp_tools_and_create_job_over_real_http(self):
        async def run():
            async with Client(f"{self.base}/{PATH_SECRET}/mcp") as client:
                tools = {t.name for t in (await client.list_tools()).tools}
                self.assertEqual(tools, EXPECTED_TOOLS)
                res = await client.call_tool("create_video_job", {"topic": "Vì sao bầu trời xanh", "duration_seconds": 20})
                data = res.structured_content
                self.assertTrue(data["ok"], data)
                jid = data["job_id"]
                st = (await client.call_tool("get_job_status", {"job_id": jid})).structured_content
                self.assertEqual(st["status"], "queued")
                bad = (await client.call_tool("create_video_job", {"topic": "x", "aspect_ratio": "4:3"})).structured_content
                self.assertFalse(bad["ok"])
                self.assertEqual(bad["error"]["code"], "invalid_payload")
                return jid
        jid = asyncio.run(run())
        self.assertIn((jid, 1), self.gh.dispatched)   # GitHub (giả) thực sự được gọi qua tool

    def test_bearer_header_accepted(self):
        r = requests.post(f"{self.base}/mcp", headers={**ACCEPT, "Authorization": f"Bearer {API_KEY}"}, json=INIT, timeout=10)
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIn("serverInfo", r.text)

    def test_unauthorized_variants(self):
        self.assertEqual(requests.post(f"{self.base}/mcp", headers=ACCEPT, json=INIT, timeout=5).status_code, 401)
        self.assertEqual(requests.post(f"{self.base}/mcp", headers={**ACCEPT, "Authorization": "Bearer wrong"}, json=INIT, timeout=5).status_code, 401)
        self.assertEqual(requests.post(f"{self.base}/{'x' * 32}/mcp", headers=ACCEPT, json=INIT, timeout=5).status_code, 401)
        self.assertEqual(requests.get(f"{self.base}/etc/passwd", timeout=5).status_code, 404)

    def test_oversized_body_rejected_before_reaching_mcp(self):
        big = {"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"pad": "a" * 20000}}
        r = requests.post(f"{self.base}/{PATH_SECRET}/mcp", headers=ACCEPT, json=big, timeout=5)
        self.assertEqual(r.status_code, 413)


if __name__ == "__main__":
    unittest.main()
