import pytest
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry


class FakeTool:
    def __init__(self, name, result_text="ok"):
        self._name = name
        self._result_text = result_text

    @property
    def schema(self) -> dict:
        return {"name": self._name, "description": "d", "parameters": {"type": "object", "properties": {}}}

    async def execute(self, ctx: ToolContext) -> ToolResult:
        return ToolResult(text=self._result_text)


@pytest.fixture
def ctx():
    return ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)


def test_register_and_schemas():
    reg = ToolRegistry()
    reg.register(FakeTool("echo"))
    schemas = reg.schemas()
    assert schemas == [{"type": "function", "function": {"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


async def test_execute_known(ctx):
    reg = ToolRegistry()
    reg.register(FakeTool("echo", "hi"))
    result = await reg.execute("echo", ctx)
    assert result.text == "hi"


async def test_execute_unknown_returns_error(ctx):
    reg = ToolRegistry()
    result = await reg.execute("nope", ctx)
    assert "未知工具" in result.text


async def test_execute_swallows_exception(ctx):
    class Boom:
        @property
        def schema(self):
            return {"name": "boom", "description": "d", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, ctx):
            raise RuntimeError("爆炸")

    reg = ToolRegistry()
    reg.register(Boom())
    result = await reg.execute("boom", ctx)
    assert "工具执行出错" in result.text
