from unittest.mock import AsyncMock, MagicMock, patch


def _chunk(content=None, tool_calls=None, finish_reason=None):
    """构造一个模拟的流式 chunk。"""
    choice = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _tool_call_delta(index=0, cid=None, name=None, args=None):
    tc = MagicMock()
    tc.index = index
    tc.id = cid
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    return tc


async def test_astream_once_text_only():
    from app.llm import LLMService
    llm = LLMService()
    fake_stream = _async_iter([
        _chunk(content="你"),
        _chunk(content="好"),
        _chunk(finish_reason="stop"),
    ])
    with patch.object(llm._client, "chat", new=_mock_chat(fake_stream)):
        events = []
        async for ev in llm.astream_once():
            events.append(ev)
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert events[-1]["tool_calls"] == []
    assert "".join(e["text"] for e in events if e["type"] == "text") == "你好"


async def test_astream_once_detects_tool_call():
    from app.llm import LLMService
    llm = LLMService()
    fake_stream = _async_iter([
        _chunk(content="我看看"),
        _chunk(tool_calls=[_tool_call_delta(0, cid="call_1", name="take_photo", args="{}")]),
        _chunk(finish_reason="tool_calls"),
    ])
    with patch.object(llm._client, "chat", new=_mock_chat(fake_stream)):
        events = [e async for e in llm.astream_once()]
    done = events[-1]
    assert done["finish_reason"] == "tool_calls"
    assert len(done["tool_calls"]) == 1
    tc = done["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "take_photo"


async def test_add_user_and_add_tool():
    from app.llm import LLMService
    llm = LLMService()
    llm.add_user("hi")
    llm.add_tool("call_1", [{"type": "text", "text": "照片"}])
    msgs = llm.messages()
    assert msgs[-2]["role"] == "user"
    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == "call_1"


# ---- helpers ----
async def _async_iter(items):
    for it in items:
        yield it


def _mock_chat(stream):
    """chat.completions.create 的 mock。"""
    completions = MagicMock()
    completions.create = AsyncMock(return_value=stream)
    chat = MagicMock()
    chat.completions = completions
    return chat
