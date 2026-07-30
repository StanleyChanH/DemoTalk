from unittest.mock import AsyncMock, MagicMock

from app.mcp.client import McpClient
from app.mcp.config import ServerConfig


def _client():
    return McpClient(ServerConfig(name="x", type="sse", url="https://e/sse"))


async def test_call_tool_extracts_text_content():
    c = _client()
    fake_text = MagicMock()
    fake_text.text = "番茄炒蛋的做法"
    fake_other = MagicMock(spec=[])  # 无 text 属性
    result = MagicMock()
    result.content = [fake_text, fake_other]
    c._session = MagicMock()
    c._session.call_tool = AsyncMock(return_value=result)

    text = await c.call_tool("whatToEat", {"k": "v"})
    c._session.call_tool.assert_called_once_with("whatToEat", {"k": "v"})
    assert text == "番茄炒蛋的做法"


async def test_call_tool_empty_args():
    c = _client()
    result = MagicMock()
    result.content = []
    c._session = MagicMock()
    c._session.call_tool = AsyncMock(return_value=result)
    text = await c.call_tool("t", None)
    c._session.call_tool.assert_called_once_with("t", {})
    assert text == ""


async def test_close_acloses_stack():
    c = _client()
    c._stack = MagicMock()
    c._stack.aclose = AsyncMock()
    await c.close()
    c._stack.aclose.assert_called_once()
