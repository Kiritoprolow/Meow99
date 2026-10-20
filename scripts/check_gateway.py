"""Kiểm tra gateway từ xa bằng client MCP chính thức.

  python scripts/check_gateway.py https://<space>.hf.space/<MCP_PATH_SECRET>/mcp
Không in secret; chỉ in tên tool và kết quả get_system_status.
"""
import asyncio
import json
import sys

from mcp import Client


async def main(url: str) -> int:
    async with Client(url) as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        print("tools:", ", ".join(tools))
        res = await c.call_tool("get_system_status", {})
        print(json.dumps(res.structured_content, ensure_ascii=False, indent=2))
        return 0 if res.structured_content.get("ok") else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1])))
