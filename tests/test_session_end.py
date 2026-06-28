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


async def test_tts_end_emits_conversation_end_when_ending(monkeypatch):
    """_ending 时 tts_end：发 tts_end + conversation_end，并调度兜底关闭（用 mock 避免真 30s）。"""
    s = _make_session()
    s._ending = True
    fake_tts = MagicMock()
    s.tts = fake_tts

    scheduled = {}

    async def fake_force_close(delay):
        scheduled["delay"] = delay

    s._force_close_after = fake_force_close

    sent = []

    async def capture_send(obj):
        sent.append(obj["type"])

    s._send = capture_send

    await s._on_tts_state("tts_end", fake_tts)
    await asyncio.sleep(0)  # 让 create_task 调度的 fake_force_close 跑完

    assert "tts_end" in sent
    assert "conversation_end" in sent
    assert scheduled["delay"] == 30.0


async def test_tts_end_sets_listening_when_not_ending(monkeypatch):
    """非结束的正常 tts_end：维持原行为（set listening），不发 conversation_end。"""
    s = _make_session()
    s._ending = False
    fake_tts = MagicMock()
    s.tts = fake_tts

    sent = []

    async def capture_send(obj):
        sent.append(obj["type"])

    s._send = capture_send

    await s._on_tts_state("tts_end", fake_tts)

    assert "conversation_end" not in sent
    assert s.state == "listening"


async def test_force_close_after_closes_ws_when_running():
    """兜底：_running=True 时 _force_close_after(0) 调 ws.close()。"""
    s = _make_session()
    s._running = True
    await s._force_close_after(0)
    s.ws.close.assert_awaited_once()


async def test_force_close_after_skips_when_not_running():
    """兜底：_running=False 时 _force_close_after(0) 不调 ws.close()。"""
    s = _make_session()
    s._running = False
    await s._force_close_after(0)
    s.ws.close.assert_not_called()


async def test_shutdown_cancels_end_fallback(monkeypatch):
    """shutdown() 取消 _end_fallback 兜底任务，避免孤儿 task 持有 Session。"""
    _stub_tts(monkeypatch)
    s = _make_session()
    # shutdown() 会调 stt.stop（可能阻塞），替换为 no-op 保证测试稳定
    s.stt.stop = lambda: None
    # 用一个真实的长 sleep task 模拟已调度的兜底任务
    s._end_fallback = asyncio.get_event_loop().create_task(asyncio.sleep(100.0))
    assert not s._end_fallback.done()

    await s.shutdown()

    assert s._end_fallback.done()
    assert s._end_fallback.cancelled()

