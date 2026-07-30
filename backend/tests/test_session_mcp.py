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


async def test_run_turn_passes_mcp_tools_when_vision_disabled(monkeypatch):
    """回归：ENABLE_VISION=False 且 ENABLE_MCP=True 时，_run_turn 仍把 MCP tool 的
    schema 传给 llm.astream_once（修复 tools 被 ENABLE_VISION 误 gate 的 bug）。"""
    import asyncio
    import app.session as session_mod
    from app.tools.base import ToolResult

    monkeypatch.setattr(session_mod.config, "ENABLE_VISION", False)
    monkeypatch.setattr(session_mod.config, "ENABLE_MCP", True)

    ws = MagicMock()
    s = session_mod.Session(ws, asyncio.new_event_loop())

    # 注入一个 MCP-style tool 到 registry（模拟 register_into 的效果）
    class FakeMcpTool:
        @property
        def schema(self):
            return {
                "name": "mcp_howtocook_whatToEat",
                "description": "推荐菜品",
                "parameters": {
                    "type": "object",
                    "properties": {"peopleCount": {"type": "number"}},
                    "required": ["peopleCount"],
                },
            }

        async def execute(self, ctx):
            return ToolResult(text="ok")

    s.tool_registry.register(FakeMcpTool())

    # mock LLM：astream_once 返回 done（无 tool_calls），捕获 tools 参数
    captured = {}

    async def fake_astream_once(tools=None):
        captured["tools"] = tools
        yield {"type": "done", "tool_calls": [], "finish_reason": "stop"}

    s.llm.astream_once = fake_astream_once
    s.llm.add_user = lambda t: None
    s.llm.add_tool = lambda cid, c: None

    # mock TTS（避免真 TTS 线程）
    fake_tts = MagicMock()
    fake_tts.start = lambda: None
    fake_tts.feed = lambda t: None
    fake_tts.finish = lambda: None
    fake_tts.cancel = lambda: None
    monkeypatch.setattr(session_mod, "TTSService", lambda **kw: fake_tts)

    await s._run_turn("晚上吃什么", turn=1)

    assert captured["tools"] is not None
    names = [t["function"]["name"] for t in captured["tools"]]
    assert "mcp_howtocook_whatToEat" in names
