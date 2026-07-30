"""空闲超时（idle timeout）相关测试：watchdog 触发、运行时开关、活动时间戳刷新。"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import config
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_running_loop()
    return Session(ws, loop), ws


def _sent_objects(ws):
    return [json.loads(c.args[0]) for c in ws.send_text.call_args_list]


class _FakeTTS:
    """替身：避免 _idle_timeout_react 真起 TTS 线程连百炼。"""

    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def feed(self, text):
        pass

    def finish(self):
        pass

    def cancel(self):
        pass


# ---- 默认值与会话属性 ----

async def test_idle_timeout_initialized_from_config():
    s, _ = _make_session()
    assert s.idle_timeout_enabled == config.ENABLE_IDLE_TIMEOUT
    assert s._last_activity == 0.0
    assert s._idle_task is None


# ---- set_flags 运行时开关 ----

async def test_set_flags_idle_timeout_toggle():
    s, _ = _make_session()
    assert s.idle_timeout_enabled is True
    await s.set_flags({"idle_timeout": False})
    assert s.idle_timeout_enabled is False
    await s.set_flags({"idle_timeout": True})
    assert s.idle_timeout_enabled is True


async def test_set_flags_idle_timeout_does_not_touch_others():
    s, _ = _make_session()
    await s.set_flags({"idle_timeout": False})
    assert s.barge_in_enabled == config.ENABLE_BARGE_IN  # 其他开关不受影响


# ---- _idle_timeout_react ----

async def test_idle_timeout_react_sets_ending_and_sends_delta(monkeypatch):
    s, ws = _make_session()
    monkeypatch.setattr("app.session.TTSService", _FakeTTS)
    s._last_activity = time.monotonic() - 999  # 远超阈值
    await s._idle_timeout_react()
    assert s._ending is True
    objs = _sent_objects(ws)
    assert any(
        o.get("type") == "delta" and o.get("text") == config.IDLE_PROMPT for o in objs
    )


async def test_idle_timeout_react_noop_when_within_window(monkeypatch):
    s, ws = _make_session()
    monkeypatch.setattr("app.session.TTSService", _FakeTTS)
    s._last_activity = time.monotonic()  # 刚活动，未超时
    await s._idle_timeout_react()
    assert s._ending is False
    assert all(o.get("type") != "delta" for o in _sent_objects(ws))


async def test_idle_timeout_react_skips_when_already_ending(monkeypatch):
    s, _ = _make_session()
    monkeypatch.setattr("app.session.TTSService", _FakeTTS)
    s._ending = True
    s._last_activity = time.monotonic() - 999
    await s._idle_timeout_react()
    assert s.tts is None  # 已在结束流程，不再起新 TTS


# ---- 活动时间戳刷新 ----

async def test_on_final_refreshes_last_activity():
    s, _ = _make_session()
    assert s._last_activity == 0.0
    s._run_turn = AsyncMock()
    try:
        await s._on_final("hello")
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()
    assert s._last_activity > 0.0


async def test_barge_in_refreshes_last_activity():
    s, _ = _make_session()
    s._last_activity = 0.0
    await s._barge_in()
    assert s._last_activity > 0.0


async def test_tts_end_refreshes_last_activity_when_not_ending():
    s, _ = _make_session()
    s._last_activity = 0.0
    s.state = "speaking"
    s.tts = MagicMock()  # 占位实例，使 _on_tts_state 的 source is self.tts 通过
    await s._on_tts_state("tts_end", s.tts)
    assert s._last_activity > 0.0
    assert s.state == "listening"


# ---- watchdog（_idle_loop）----

async def test_idle_loop_triggers_after_timeout(monkeypatch):
    s, _ = _make_session()
    monkeypatch.setattr(config, "IDLE_TIMEOUT", 0)
    s.state = "listening"
    s._last_activity = time.monotonic() - 1  # 已超时
    called = []

    async def fake_react():
        called.append(1)

    s._idle_timeout_react = fake_react
    await asyncio.wait_for(s._idle_loop(), timeout=3)
    assert called == [1]


async def test_idle_loop_skips_when_speaking(monkeypatch):
    s, _ = _make_session()
    monkeypatch.setattr(config, "IDLE_TIMEOUT", 0)
    s.state = "speaking"  # 非 listening，不应触发
    s._last_activity = time.monotonic() - 100
    s._idle_timeout_react = AsyncMock()
    task = asyncio.create_task(s._idle_loop())
    await asyncio.sleep(1.2)  # 跑过至少一次 sleep(1) 复查
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert s._idle_timeout_react.await_count == 0


async def test_idle_loop_disabled_does_not_trigger(monkeypatch):
    s, _ = _make_session()
    monkeypatch.setattr(config, "IDLE_TIMEOUT", 0)
    s.idle_timeout_enabled = False
    s.state = "listening"
    s._last_activity = time.monotonic() - 100
    s._idle_timeout_react = AsyncMock()
    task = asyncio.create_task(s._idle_loop())
    await asyncio.sleep(1.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert s._idle_timeout_react.await_count == 0
