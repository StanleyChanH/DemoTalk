from app.tools.registry import ToolRegistry
from app.tools.base import Tool, ToolContext, ToolResult


class _FakeTool(Tool):
    def __init__(self, name: str):
        self._schema = {
            "name": name,
            "description": "fake",
            "parameters": {"type": "object", "properties": {}},
        }

    @property
    def schema(self) -> dict:
        return self._schema

    async def execute(self, ctx: ToolContext) -> ToolResult:
        return ToolResult(text=f"exec:{self._schema['name']}")


async def _noop_photo(_call_id: str) -> str | None:
    return None


def test_register_default_source():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    assert r.get("a") is not None
    assert r.sources() == {"a": "builtin"}


def test_register_with_source():
    r = ToolRegistry()
    r.register(_FakeTool("mcp_x"), source="mcp")
    assert r.sources() == {"mcp_x": "mcp"}


def test_unregister_removes_tool():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    r.unregister("a")
    assert r.get("a") is None
    assert r.schemas() == []


def test_unregister_missing_is_noop():
    r = ToolRegistry()
    r.unregister("nope")  # 不应抛异常


def test_clear_by_source_removes_only_that_source():
    r = ToolRegistry()
    r.register(_FakeTool("builtin1"))
    r.register(_FakeTool("mcp_a"), source="mcp")
    r.register(_FakeTool("mcp_b"), source="mcp")
    r.clear_by_source("mcp")
    assert set(r.sources()) == {"builtin1"}


def test_clear_by_source_keeps_builtin():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    r.register(_FakeTool("b"), source="mcp")
    r.clear_by_source("mcp")
    assert set(r.sources()) == {"a"}


def test_schemas_format():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    assert r.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "a",
                "description": "fake",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def test_execute_registered_tool():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    ctx = ToolContext(call_id="1", args={}, request_photo=_noop_photo)
    result = await r.execute("a", ctx)
    assert result.text == "exec:a"


async def test_execute_unknown_tool():
    r = ToolRegistry()
    ctx = ToolContext(call_id="1", args={}, request_photo=_noop_photo)
    result = await r.execute("missing", ctx)
    assert "未知工具" in result.text


class _BoomTool(Tool):
    @property
    def schema(self) -> dict:
        return {"name": "boom", "description": "raises", "parameters": {"type": "object", "properties": {}}}

    async def execute(self, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("boom")


async def test_execute_swallows_exception():
    r = ToolRegistry()
    r.register(_BoomTool())
    ctx = ToolContext(call_id="1", args={}, request_photo=_noop_photo)
    result = await r.execute("boom", ctx)
    assert "工具执行出错" in result.text
