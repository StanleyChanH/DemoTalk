from unittest.mock import AsyncMock, MagicMock

from app.mcp.config import ServerConfig
from app.mcp.manager import McpManager
from app.tools.registry import ToolRegistry


def _fake_client(name, tools, fail_connect=False):
    client = MagicMock()
    client.config = ServerConfig(name=name, type="sse", url="https://e/sse")
    if fail_connect:
        client.connect = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        client.connect = AsyncMock()
        client.list_tools = AsyncMock(return_value=tools)
        client.call_tool = AsyncMock(return_value="result")
        client.close = AsyncMock()
    return client


async def test_load_all_registers_adapters():
    m = McpManager()
    t1 = MagicMock(); t1.name = "tA"; t1.description = "dA"; t1.inputSchema = {"type": "object", "properties": {}}
    m._make_client = lambda cfg: _fake_client(cfg.name, [t1])
    await m.load_all([ServerConfig(name="s1", type="sse", url="u")])

    reg = ToolRegistry()
    m.register_into(reg)
    schemas = reg.schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "tA"


async def test_load_all_skips_failed_server():
    m = McpManager()
    t1 = MagicMock(); t1.name = "tA"; t1.description = "dA"; t1.inputSchema = {"type": "object", "properties": {}}
    configs = [
        ServerConfig(name="bad", type="sse", url="u1"),
        ServerConfig(name="good", type="sse", url="u2"),
    ]
    def make(cfg):
        return _fake_client(cfg.name, [t1], fail_connect=(cfg.name == "bad"))
    m._make_client = make
    await m.load_all(configs)

    reg = ToolRegistry()
    m.register_into(reg)
    assert len(reg.schemas()) == 1  # 只有 good 的 tA


async def test_close_all_closes_clients():
    m = McpManager()
    c = _fake_client("s", [])
    m._make_client = lambda cfg: c
    await m.load_all([ServerConfig(name="s", type="sse", url="u")])
    await m.close_all()
    c.close.assert_awaited_once()


# --- feature-toggles: source 标记 + has_tools() ---

class _FakeAdapter:
    """模拟 McpToolAdapter：registry 只用到 .schema['name']。"""

    def __init__(self, name: str):
        self._name = name

    @property
    def schema(self):
        return {
            "name": self._name,
            "description": "mcp tool",
            "parameters": {"type": "object", "properties": {}},
        }


def test_has_tools_false_when_empty():
    assert McpManager().has_tools() is False


def test_has_tools_true_with_adapters():
    m = McpManager()
    m._adapters = [_FakeAdapter("x")]
    assert m.has_tools() is True


def test_register_into_marks_mcp_source():
    m = McpManager()
    m._adapters = [_FakeAdapter("mcp_a"), _FakeAdapter("mcp_b")]
    r = ToolRegistry()
    m.register_into(r)
    assert r.sources() == {"mcp_a": "mcp", "mcp_b": "mcp"}


def test_clear_by_source_after_register_into():
    m = McpManager()
    m._adapters = [_FakeAdapter("mcp_a")]
    r = ToolRegistry()
    r.register(_FakeAdapter("builtin1"))
    m.register_into(r)
    r.clear_by_source("mcp")
    assert set(r.sources()) == {"builtin1"}
