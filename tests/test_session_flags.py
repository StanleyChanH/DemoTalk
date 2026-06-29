import asyncio
import json
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


class _FakeAdapter:
    def __init__(self, name):
        self._name = name

    @property
    def schema(self):
        return {"name": self._name, "description": "mcp", "parameters": {"type": "object", "properties": {}}}


# ---- barge_in 会话属性 ----

async def test_barge_in_initialized_from_config():
    s, _ = _make_session()
    assert s.barge_in_enabled == config.ENABLE_BARGE_IN


async def test_set_flags_barge_in():
    s, _ = _make_session()
    await s.set_flags({"barge_in": False})
    assert s.barge_in_enabled is False
    await s.set_flags({"barge_in": True})
    assert s.barge_in_enabled is True


async def test_set_flags_partial_does_not_touch_others():
    s, _ = _make_session()
    await s.set_flags({"barge_in": False})
    assert s.barge_in_enabled is False
    # end_conversation 不受影响（config 默认 True，已注册）
    assert s.tool_registry.get("end_conversation") is not None


async def test_on_final_no_barge_in_when_disabled():
    s, _ = _make_session()
    s.barge_in_enabled = False
    s.state = "speaking"
    called = []

    async def fake_barge():
        called.append(1)

    s._barge_in = fake_barge
    s._run_turn = AsyncMock()
    try:
        await s._on_final("hi")
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()
    assert called == []  # 禁用时不打断


async def test_on_final_barge_in_when_enabled():
    s, _ = _make_session()
    s.barge_in_enabled = True
    s.state = "speaking"
    called = []

    async def fake_barge():
        called.append(1)

    s._barge_in = fake_barge
    s._run_turn = AsyncMock()
    try:
        await s._on_final("hi")
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()
    assert called == [1]  # 启用时打断


# ---- end_by_voice 动态增删 ----

async def test_set_flags_end_by_voice_toggle():
    s, _ = _make_session()
    assert s.tool_registry.get("end_conversation") is not None  # 默认注册
    await s.set_flags({"end_by_voice": False})
    assert s.tool_registry.get("end_conversation") is None
    await s.set_flags({"end_by_voice": True})
    assert s.tool_registry.get("end_conversation") is not None


# ---- mcp 动态增删 ----

async def test_set_flags_mcp_toggle(monkeypatch):
    from app.mcp import manager as mmod

    s, _ = _make_session()

    def fake_register(registry):
        registry.register(_FakeAdapter("mcp_x"), source="mcp")

    monkeypatch.setattr(mmod.mcp_manager, "register_into", fake_register)

    await s.set_flags({"mcp": True})
    assert s.tool_registry.sources().get("mcp_x") == "mcp"
    await s.set_flags({"mcp": False})
    assert "mcp_x" not in s.tool_registry.sources()
    await s.set_flags({"mcp": True})
    assert s.tool_registry.sources().get("mcp_x") == "mcp"  # 重新开启恢复工具


# ---- config_defaults 下发 ----

async def test_start_emits_config_defaults(monkeypatch):
    s, ws = _make_session()
    monkeypatch.setattr(s.stt, "start", lambda: None)  # 避免连 STT SDK
    try:
        await s.start()
        objs = _sent_objects(ws)
        cd = next((o for o in objs if o.get("type") == "config_defaults"), None)
        assert cd is not None
        assert cd["barge_in"] == config.ENABLE_BARGE_IN
        assert cd["mcp"] == config.ENABLE_MCP
        assert cd["end_by_voice"] == config.ENABLE_END_BY_VOICE
        assert cd["idle_timeout"] == config.ENABLE_IDLE_TIMEOUT
        assert "mcp_available" in cd
        assert cd["vad_sensitivity"] == config.VAD_SENSITIVITY
    finally:
        # start() 会起 _idle_task 看门狗，测试结束前回收避免孤儿
        if s._idle_task and not s._idle_task.done():
            s._idle_task.cancel()
            try:
                await s._idle_task
            except asyncio.CancelledError:
                pass
