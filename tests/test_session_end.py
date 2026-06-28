import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    loop = asyncio.get_event_loop()
    s = Session(ws, loop)
    s._running = True
    return s


def _stub_tts(monkeypatch):
    """用轻量 stub 替换 TTSService，避免真实网络/线程。"""
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


async def test_run_turn_ends_on_end_conversation_tool(monkeypatch):
    """LLM 返回 tool_calls=[end_conversation] 时：置 _ending=True 且不再发起第二次 LLM 调用。"""
    _stub_tts(monkeypatch)
    monkeypatch.setattr("app.config.ENABLE_END_BY_VOICE", True)  # 确保注册了 end_conversation
    s = _make_session()
    s._current_turn = 1

    end_tool_call = {"id": "call_e", "function": {"name": "end_conversation", "arguments": "{}"}}

    call_count = {"n": 0}

    async def fake_astream_once(tools=None):
        call_count["n"] += 1
        # 真实实现会在流结束时 append assistant(tool_calls)
        s.llm._history.append({"role": "assistant", "content": "好的，再见！", "tool_calls": [end_tool_call]})
        yield {"type": "text", "text": "好的，再见！"}
        yield {"type": "done", "tool_calls": [end_tool_call], "finish_reason": "tool_calls"}

    s.llm.astream_once = fake_astream_once
    s.llm.add_user = lambda t: None
    s.llm.add_tool = lambda cid, c: None

    await s._run_turn("再见", turn=1)

    assert s._ending is True
    assert call_count["n"] == 1  # 只调一次 LLM，没有为 tool 结果再发第二次


async def test_on_final_ignored_when_ending(monkeypatch):
    """_ending=True 时 _on_final 直接返回：不发 user_final、不建 turn task、不自增 turn。"""
    s = _make_session()
    s._ending = True
    s._current_turn = 0
    s.ws.send_text = AsyncMock()

    await s._on_final("还在吗")

    assert s._current_turn == 0
    assert s._turn_task is None
    s.ws.send_text.assert_not_called()  # 连 user_final 都没发


def test_registers_end_conversation_when_enabled(monkeypatch):
    """ENABLE_END_BY_VOICE=True 时 registry 含 end_conversation。"""
    import app.session as session_mod
    monkeypatch.setattr(session_mod.config, "ENABLE_END_BY_VOICE", True)
    monkeypatch.setattr(session_mod.config, "ENABLE_VISION", False)
    monkeypatch.setattr(session_mod.config, "ENABLE_MCP", False)

    s = session_mod.Session(MagicMock(), asyncio.new_event_loop())
    names = [t["function"]["name"] for t in s.tool_registry.schemas()]
    assert "end_conversation" in names


def test_skips_end_conversation_when_disabled(monkeypatch):
    """ENABLE_END_BY_VOICE=False 时 registry 不含 end_conversation。"""
    import app.session as session_mod
    monkeypatch.setattr(session_mod.config, "ENABLE_END_BY_VOICE", False)
    monkeypatch.setattr(session_mod.config, "ENABLE_VISION", False)
    monkeypatch.setattr(session_mod.config, "ENABLE_MCP", False)

    s = session_mod.Session(MagicMock(), asyncio.new_event_loop())
    names = [t["function"]["name"] for t in s.tool_registry.schemas()]
    assert "end_conversation" not in names
