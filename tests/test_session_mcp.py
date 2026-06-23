from unittest.mock import MagicMock

from app.session import Session


def test_session_registers_mcp_adapters(monkeypatch):
    # mock mcp_manager.register_into 记录被调用
    import app.session as session_mod
    called = {}

    def fake_register(registry):
        called["registry"] = registry

    monkeypatch.setattr(session_mod.mcp_manager, "register_into", fake_register)
    monkeypatch.setattr(session_mod.config, "ENABLE_MCP", True)

    ws = MagicMock()
    import asyncio
    s = Session(ws, asyncio.new_event_loop())
    assert called["registry"] is s.tool_registry


def test_session_skips_mcp_when_disabled(monkeypatch):
    import app.session as session_mod
    monkeypatch.setattr(session_mod.config, "ENABLE_MCP", False)
    monkeypatch.setattr(session_mod.mcp_manager, "register_into", lambda r: (_ for _ in ()).throw(AssertionError("不应调用")))
    import asyncio
    ws = MagicMock()
    Session(ws, asyncio.new_event_loop())  # 不应抛 AssertionError
