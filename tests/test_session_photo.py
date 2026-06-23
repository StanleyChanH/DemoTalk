import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_event_loop()
    s = Session(ws, loop)
    s._running = True
    return s


async def test_request_photo_returns_data_when_resolved():
    s = _make_session()
    ws = s.ws

    async def fake_send(payload):
        # _send 下发的是 JSON 字符串；解析后模拟前端回传 photo
        obj = json.loads(payload)
        if obj.get("type") != "take_photo":
            return
        call_id = obj["call_id"]
        asyncio.get_event_loop().call_later(
            0.05, lambda: asyncio.ensure_future(s.resolve_photo(call_id, "data:image/jpeg;base64,X"))
        )

    ws.send_text = fake_send
    data = await s.request_photo("c1")
    assert data == "data:image/jpeg;base64,X"
    assert "c1" not in s._pending_photos


async def test_request_photo_timeout_returns_none(monkeypatch):
    s = _make_session()
    monkeypatch.setattr("app.config.TAKE_PHOTO_TIMEOUT", 0)  # 立即超时
    s.ws.send_text = AsyncMock()
    data = await s.request_photo("c2")
    assert data is None


async def test_resolve_photo_ignores_unknown_call_id():
    s = _make_session()
    await s.resolve_photo("nope", "data")  # 不应抛错
    assert s._pending_photos == {}


async def test_barge_in_during_photo_keeps_history_consistent(monkeypatch):
    """barge-in 在拍照等待期间触发时，history 仍应以 tool 响应收尾，
    紧跟 assistant(tool_calls)，不出现孤立的 tool_calls。"""
    from app.tools.base import ToolResult

    s = _make_session()
    s.ws.send_text = AsyncMock()
    s.ws.send_bytes = AsyncMock()

    # 用轻量 stub 替换 TTSService，避免真实网络/线程
    class _FakeTTS:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def feed(self, text):
            pass

        def finish(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("app.session.TTSService", _FakeTTS)

    # 模拟 astream_once：emit 一个 done 事件，携带一个 tool_call，
    # 同时（正如真实实现那样）把 assistant(tool_calls) append 到 _history。
    tool_call = {"id": "call_x", "function": {"name": "take_photo", "arguments": "{}"}}

    async def fake_astream_once(tools=None):
        # 真实实现会在流结束时 append 这条 assistant 消息
        s.llm._history.append({"role": "assistant", "content": "", "tool_calls": [tool_call]})
        yield {"type": "done", "tool_calls": [tool_call], "finish_reason": "tool_calls"}

    s.llm.astream_once = fake_astream_once

    # 模拟 registry.execute：在“等待拍照”期间发生 barge-in（_current_turn 自增），
    # 之后仍返回一个正常结果。
    async def fake_execute(name, ctx):
        s._current_turn += 1  # 模拟 barge-in
        return ToolResult(text="照片已拍")

    s.tool_registry.execute = fake_execute

    # 跑一轮：turn=1（同步 _current_turn 使 active() 初始为 True）；
    # execute 期间 barge-in 使 _current_turn 变 2 → active() 为 False
    s._current_turn = 1
    await s._run_turn("看下我", turn=1)

    history = s.llm._history
    # 找到最后一条 assistant(tool_calls)
    idx = max(i for i, m in enumerate(history) if m.get("role") == "assistant" and m.get("tool_calls"))
    # 紧随其后的必须是 tool 响应（API 一致性）
    assert history[idx + 1]["role"] == "tool"
    assert history[idx + 1]["tool_call_id"] == "call_x"
    # history 不能以孤立的 assistant(tool_calls) 结尾
    assert history[-1]["role"] != "assistant" or not history[-1].get("tool_calls")
