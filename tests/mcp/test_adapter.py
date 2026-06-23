from unittest.mock import AsyncMock, MagicMock

from app.tools.base import ToolContext
from app.mcp.adapter import McpToolAdapter


def _mcp_tool(name="whatToEat", desc="推荐菜品", schema=None):
    t = MagicMock()
    t.name = name
    t.description = desc
    t.inputSchema = schema or {"type": "object", "properties": {}}
    return t


def test_schema_from_mcp_tool():
    client = MagicMock()
    a = McpToolAdapter(_mcp_tool("t", "d"), client)
    s = a.schema
    assert s["name"] == "t"
    assert s["description"] == "d"
    assert s["parameters"] == {"type": "object", "properties": {}}


async def test_execute_forwards_args_and_wraps_result():
    client = MagicMock()
    client.call_tool = AsyncMock(return_value="番茄炒蛋")
    a = McpToolAdapter(_mcp_tool(), client)

    async def fake_request(call_id):
        return None

    ctx = ToolContext(call_id="c1", args={"pref": "清淡"}, request_photo=fake_request)
    result = await a.execute(ctx)
    client.call_tool.assert_awaited_once_with("whatToEat", {"pref": "清淡"})
    assert result.text == "番茄炒蛋"
    assert result.image_data_url is None
