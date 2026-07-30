import pytest
from app.tools.base import ToolContext
from app.tools.builtin.end_conversation import EndConversationTool


@pytest.fixture
def tool():
    return EndConversationTool()


def test_schema(tool):
    s = tool.schema
    assert s["name"] == "end_conversation"
    assert s["parameters"] == {"type": "object", "properties": {}}
    assert "结束" in s["description"]


async def test_execute_invokes_request_end_conversation(tool):
    called = {"n": 0}

    async def fake_end():
        called["n"] += 1

    ctx = ToolContext(
        call_id="c1",
        args={},
        request_photo=lambda cid: None,
        request_end_conversation=fake_end,
    )
    result = await tool.execute(ctx)
    assert called["n"] == 1  # 回调被 await 调用
    assert isinstance(result.text, str)


async def test_execute_ok_when_no_callback(tool):
    # 兼容：ctx 未注入回调时不报错（防御）
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    result = await tool.execute(ctx)
    assert isinstance(result.text, str)
