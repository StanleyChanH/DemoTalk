"""VAD speech_end 提前触发 turn-end + 与 STT final 去重的测试。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app import config
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_running_loop()
    s = Session(ws, loop)
    s._running = True
    return s, ws


async def test_speech_end_uses_partial_to_begin_turn():
    """listening 期收到 speech_end：用缓存 partial 开轮、发 user_final、置 _endpoint_done。"""
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = "你好世界"
    s._run_turn = AsyncMock()
    sent = []

    async def cap(obj):
        sent.append(obj["type"])

    s._send = cap
    try:
        await s._on_speech_end()
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()

    assert s._endpoint_done is True
    s._run_turn.assert_called_once()
    assert "user_final" in sent


async def test_speech_end_ignored_when_not_listening():
    """thinking/speaking 期不处理 speech_end（避免回声误触 / 干扰进行中的轮）。"""
    s, _ = _make_session()
    s.state = "speaking"
    s.stt._last_partial = "你好"
    s._run_turn = AsyncMock()

    await s._on_speech_end()

    s._run_turn.assert_not_called()


async def test_speech_end_ignored_when_no_partial():
    """暂无 partial：忽略，等 STT final 兜底。"""
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = ""
    s._run_turn = AsyncMock()

    await s._on_speech_end()

    s._run_turn.assert_not_called()


async def test_speech_end_disabled_falls_back_to_final(monkeypatch):
    """ENABLE_VAD_TURN_END=False：完全回退纯 STT final 路径。"""
    monkeypatch.setattr(config, "ENABLE_VAD_TURN_END", False)
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = "你好"
    s._run_turn = AsyncMock()

    await s._on_speech_end()

    s._run_turn.assert_not_called()


async def test_final_dropped_after_speech_end():
    """speech_end 先开轮后，延迟到达的同源 STT final 被丢弃，不重复开轮 / 不自我打断。"""
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = "你好世界"
    s._run_turn = AsyncMock()
    s._send = AsyncMock()
    try:
        await s._on_speech_end()
        assert s._endpoint_done is True
        n_after_se = s._run_turn.call_count

        await s._on_final("你好世界")  # 模拟 ~800ms 后同源 final 到达

        assert s._run_turn.call_count == n_after_se  # 未重复开轮
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()


async def test_speech_end_dropped_after_final():
    """反过来：STT final 先到开轮，speech_end 后到被丢弃。"""
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = "你好"
    s._run_turn = AsyncMock()
    s._send = AsyncMock()
    try:
        await s._on_final("你好")  # 先开轮
        assert s._endpoint_done is True
        n = s._run_turn.call_count

        await s._on_speech_end()  # 后到，丢弃

        assert s._run_turn.call_count == n
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()


async def test_set_state_listening_resets_endpoint_done():
    """回到 listening 重置去重标志，准备接收下一轮。"""
    s, _ = _make_session()
    s._endpoint_done = True

    await s._set_state("listening")

    assert s._endpoint_done is False


async def test_handle_control_speech_end_routes_to_handler():
    """handle_control 把 speech_end 转给 _on_speech_end。"""
    s, _ = _make_session()
    s.state = "listening"
    s.stt._last_partial = "你好"
    s._run_turn = AsyncMock()
    s._send = AsyncMock()
    try:
        await s.handle_control({"type": "speech_end"})
        s._run_turn.assert_called_once()
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()
