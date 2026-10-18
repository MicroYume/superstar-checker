"""冒烟测试：以 MCP 客户端身份连上 stdio 服务器，验证握手、工具列表和无登录时的错误处理。

运行: .venv\\Scripts\\python.exe tests\\smoke_test.py
"""

import asyncio
import sys


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "superstar_checker.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", ", ".join(sorted(names)))
            assert ("login" in names and "start_studying" in names
                    and "study_status" in names and "save_homework" in names
                    and "submit_homework" not in names)

            # 无会话时应优雅返回 error 而不是崩
            res = await session.call_tool("login_status", {})
            print("login_status:", res.content[0].text[:200])
            res = await session.call_tool("get_courses", {})
            print("get_courses:", res.content[0].text[:200])
            assert not res.isError, "get_courses 应返回结构化错误而非 isError"
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
