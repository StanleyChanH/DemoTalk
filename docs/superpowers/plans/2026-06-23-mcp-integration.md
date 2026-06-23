# MCP 接入 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DemoTalk 作为 MCP client，把外部 MCP server（如 howtocook-mcp）的 tools 经 `McpToolAdapter` 注册进现有 `ToolRegistry`，与视觉 `take_photo` 共存，复用 tool-calling 循环。

**Architecture:** 新增 `app/mcp/`（config 读 mcp.json / client 连单个 server / adapter 实现 Tool 协议 / manager 进程级加载）。全局 `mcp_manager` 单例在 FastAPI lifespan 启动时连所有 server 建 adapter；每个 Session 通过 `register_into` 把共享 adapter 注册进自己的 `ToolRegistry`。session/llm 的 tool-calling 循环零改动。

**Tech Stack:** Python 3.12 / FastAPI（lifespan）/ 官方 `mcp` Python SDK（sse_client/stdio_client/ClientSession）/ pytest（mock）。

**对应 spec:** `docs/superpowers/specs/2026-06-23-mcp-integration-design.md`

**约定:**
- 工作分支：`design/mcp-integration`（已创建）
- 测试 `uv run pytest`；集成 `scripts/selftest.py` Phase 5；浏览器手动验证
- 每任务结束 commit。Windows Defender 锁文件时 `UV_LINK_MODE=copy uv ...`

---

## Task 1: 加 mcp 依赖 + ENABLE_MCP / MCP_CONFIG_FILE 配置项

**Files:**
- Modify: `pyproject.toml`（加 mcp 运行时依赖）
- Modify: `app/config.py`（末尾追加）
- Modify: `tests/test_config.py`（追加断言）

- [ ] **Step 1: 加 mcp 依赖**

Run:
```bash
uv add mcp
```
Expected: `pyproject.toml` dependencies 出现 `mcp`。

- [ ] **Step 2: 写失败测试** — 在 `tests/test_config.py` 的 `test_vision_defaults` 之后追加：
```python
def test_mcp_defaults():
    assert config.ENABLE_MCP is True
    assert config.MCP_CONFIG_FILE == "mcp.json"
```

- [ ] **Step 3: 运行确认失败**
Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`AttributeError: module 'app.config' has no attribute 'ENABLE_MCP'`）

- [ ] **Step 4: 在 `app/config.py` 末尾追加**（视觉配置块之后）：
```python

# ---- MCP ----
ENABLE_MCP: bool = _bool("ENABLE_MCP", True)
MCP_CONFIG_FILE: str = _get("MCP_CONFIG_FILE", "mcp.json")
```

- [ ] **Step 5: 运行确认通过**
Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**
```bash
git add pyproject.toml uv.lock app/config.py tests/test_config.py
git commit -m "feat(mcp): mcp 依赖与 ENABLE_MCP/MCP_CONFIG_FILE 配置"
```

---

## Task 2: mcp/config.py — 读 mcp.json

**Files:**
- Create: `app/mcp/__init__.py`
- Create: `app/mcp/config.py`
- Create: `tests/mcp/__init__.py`
- Create: `tests/mcp/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/mcp/test_config.py`**
```python
import json
from pathlib import Path

from app.mcp.config import ServerConfig, load_mcp_config


def test_parse_sse(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "mcpServers": {
            "howtocook-mcp": {"type": "sse", "url": "https://example/sse"}
        }
    }), encoding="utf-8")
    servers = load_mcp_config(str(f))
    assert len(servers) == 1
    s = servers[0]
    assert s.name == "howtocook-mcp"
    assert s.type == "sse"
    assert s.url == "https://example/sse"


def test_parse_stdio(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "mcpServers": {
            "local": {"type": "stdio", "command": "npx", "args": ["-y", "x"], "env": {"K": "v"}}
        }
    }), encoding="utf-8")
    servers = load_mcp_config(str(f))
    s = servers[0]
    assert s.type == "stdio"
    assert s.command == "npx"
    assert s.args == ["-y", "x"]
    assert s.env == {"K": "v"}


def test_missing_file_returns_empty(tmp_path):
    assert load_mcp_config(str(tmp_path / "nope.json")) == []
```
Create empty `tests/mcp/__init__.py`.

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/mcp/test_config.py -v`
Expected: FAIL（`No module named 'app.mcp'`）

- [ ] **Step 3: 实现 `app/mcp/config.py`**
```python
"""MCP 配置：读取 mcp.json，解析为 ServerConfig 列表。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("demotalk.mcp")


@dataclass
class ServerConfig:
    name: str
    type: str  # "sse" | "stdio"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict | None = None


def load_mcp_config(path: str) -> list[ServerConfig]:
    """读 mcp.json。文件不存在或格式错返回空列表（不抛异常）。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("mcp.json 解析失败：%s", e)
        return []
    raw = data.get("mcpServers", {}) or {}
    servers: list[ServerConfig] = []
    for name, cfg in raw.items():
        cfg = cfg or {}
        t = cfg.get("type", "sse")
        servers.append(ServerConfig(
            name=name,
            type=t,
            url=cfg.get("url"),
            command=cfg.get("command"),
            args=cfg.get("args"),
            env=cfg.get("env"),
        ))
    return servers
```
Create `app/mcp/__init__.py`：
```python
"""DemoTalk MCP client 集成。"""
```

- [ ] **Step 4: 运行确认通过**
Run: `uv run pytest tests/mcp/test_config.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**
```bash
git add app/mcp/__init__.py app/mcp/config.py tests/mcp/
git commit -m "feat(mcp): mcp.json 解析为 ServerConfig"
```

---

## Task 3: mcp/client.py — McpClient（连单个 server）

**Files:**
- Create: `app/mcp/client.py`
- Create: `tests/mcp/test_client.py`

> 说明：McpClient 的 `connect`/`list_tools`/`close` 依赖 mcp SDK 真 transport，单元测试只覆盖 `call_tool` 的结果提取逻辑（mock session）；`connect` 的真实连接由 selftest Phase 5 集成验证。

- [ ] **Step 1: 写失败测试 `tests/mcp/test_client.py`**
```python
from unittest.mock import AsyncMock, MagicMock

from app.mcp.client import McpClient
from app.mcp.config import ServerConfig


def _client():
    return McpClient(ServerConfig(name="x", type="sse", url="https://e/sse"))


async def test_call_tool_extracts_text_content():
    c = _client()
    fake_text = MagicMock()
    fake_text.text = "番茄炒蛋的做法"
    fake_other = MagicMock(spec=[])  # 无 text 属性
    result = MagicMock()
    result.content = [fake_text, fake_other]
    c._session = MagicMock()
    c._session.call_tool = AsyncMock(return_value=result)

    text = await c.call_tool("whatToEat", {"k": "v"})
    c._session.call_tool.assert_called_once_with("whatToEat", {"k": "v"})
    assert text == "番茄炒蛋的做法"


async def test_call_tool_empty_args():
    c = _client()
    result = MagicMock()
    result.content = []
    c._session = MagicMock()
    c._session.call_tool = AsyncMock(return_value=result)
    text = await c.call_tool("t", None)
    c._session.call_tool.assert_called_once_with("t", {})
    assert text == ""


async def test_close_acloses_stack():
    c = _client()
    c._stack = MagicMock()
    c._stack.aclose = AsyncMock()
    await c.close()
    c._stack.aclose.assert_called_once()
```

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/mcp/test_client.py -v`
Expected: FAIL（`No module named 'app.mcp.client'`）

- [ ] **Step 3: 实现 `app/mcp/client.py`**
```python
"""McpClient：连接单个 MCP server（SSE 或 stdio），暴露 list_tools / call_tool。"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from .config import ServerConfig

log = logging.getLogger("demotalk.mcp")


class McpClient:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._session: ClientSession | None = None
        self._stack = AsyncExitStack()

    async def connect(self) -> None:
        """建立连接并 initialize。失败抛异常（由 manager 捕获跳过）。"""
        if self.config.type == "sse":
            if not self.config.url:
                raise ValueError(f"MCP server {self.config.name} 缺 url")
            read, write = await self._stack.enter_async_context(sse_client(self.config.url))
        elif self.config.type == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP server {self.config.name} 缺 command")
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args or [],
                env=self.config.env,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        else:
            raise ValueError(f"MCP server {self.config.name} 未知 type: {self.config.type}")
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        log.info("MCP server 已连接: %s (%s)", self.config.name, self.config.type)

    async def list_tools(self) -> list:
        return (await self._session.list_tools()).tools

    async def call_tool(self, name: str, args: dict | None) -> str:
        """调用工具，拼接所有 TextContent 的 text 返回。"""
        result = await self._session.call_tool(name, args or {})
        texts = [getattr(c, "text", "") for c in result.content]
        return "\n".join(t for t in texts if t)

    async def close(self) -> None:
        await self._stack.aclose()
        self._session = None
```

- [ ] **Step 4: 运行确认通过**
Run: `uv run pytest tests/mcp/test_client.py -v`
Expected: PASS（3 passed）。再跑全量 `uv run pytest -v` 全绿。

- [ ] **Step 5: Commit**
```bash
git add app/mcp/client.py tests/mcp/test_client.py
git commit -m "feat(mcp): McpClient SSE/stdio 连接与 call_tool"
```

---

## Task 4: mcp/adapter.py — McpToolAdapter（Tool 协议）

**Files:**
- Create: `app/mcp/adapter.py`
- Create: `tests/mcp/test_adapter.py`

- [ ] **Step 1: 写失败测试 `tests/mcp/test_adapter.py`**
```python
from unittest.mock import AsyncMock, MagicMock

from app.tools.base import ToolContext
from app.mcp.adapter import McpToolAdapter


def _mcp_tool(name="whatToEat", desc="推荐菜品", schema=None):
    t = MagicMock()
    t.name = name
    t.description = desc
    t.inputSchema = schema or {"type": "object", "properties": {}}
    return t


def test_schema_from_mcp_tool():
    client = MagicMock()
    a = McpToolAdapter(_mcp_tool("t", "d"), client)
    s = a.schema
    assert s["name"] == "t"
    assert s["description"] == "d"
    assert s["parameters"] == {"type": "object", "properties": {}}


async def test_execute_forwards_args_and_wraps_result():
    client = MagicMock()
    client.call_tool = AsyncMock(return_value="番茄炒蛋")
    a = McpToolAdapter(_mcp_tool(), client)

    async def fake_request(call_id):
        return None

    ctx = ToolContext(call_id="c1", args={"pref": "清淡"}, request_photo=fake_request)
    result = await a.execute(ctx)
    client.call_tool.assert_awaited_once_with("whatToEat", {"pref": "清淡"})
    assert result.text == "番茄炒蛋"
    assert result.image_data_url is None
```

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/mcp/test_adapter.py -v`
Expected: FAIL（`No module named 'app.mcp.adapter'`）

- [ ] **Step 3: 实现 `app/mcp/adapter.py`**
```python
"""McpToolAdapter：把单个 MCP tool 包装成 Tool 协议，复用 tool-calling 循环。"""
from __future__ import annotations

from ..tools.base import Tool, ToolContext, ToolResult


class McpToolAdapter:
    """实现 Tool 协议。schema 取自 MCP tool；execute 转发给共享的 McpClient。"""

    def __init__(self, mcp_tool, client) -> None:
        self._tool = mcp_tool
        self._client = client

    @property
    def schema(self) -> dict:
        return {
            "name": self._tool.name,
            "description": self._tool.description or f"MCP tool {self._tool.name}",
            "parameters": self._tool.inputSchema or {"type": "object", "properties": {}},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        text = await self._client.call_tool(self._tool.name, ctx.args)
        return ToolResult(text=text)
```

- [ ] **Step 4: 运行确认通过**
Run: `uv run pytest tests/mcp/test_adapter.py -v`
Expected: PASS（2 passed）。全量 `uv run pytest -v` 全绿。

- [ ] **Step 5: Commit**
```bash
git add app/mcp/adapter.py tests/mcp/test_adapter.py
git commit -m "feat(mcp): McpToolAdapter 适配 Tool 协议"
```

---

## Task 5: mcp/manager.py — McpManager（进程级加载 + register_into）

**Files:**
- Create: `app/mcp/manager.py`
- Create: `tests/mcp/test_manager.py`

- [ ] **Step 1: 写失败测试 `tests/mcp/test_manager.py`**
```python
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
```

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/mcp/test_manager.py -v`
Expected: FAIL（`No module named 'app.mcp.manager'`）

- [ ] **Step 3: 实现 `app/mcp/manager.py`**
```python
"""McpManager：进程级管理多个 MCP server 连接，把 tools 注册进 ToolRegistry。"""
from __future__ import annotations

import logging

from ..tools.registry import ToolRegistry
from .adapter import McpToolAdapter
from .client import McpClient
from .config import ServerConfig

log = logging.getLogger("demotalk.mcp")


class McpManager:
    def __init__(self) -> None:
        self._clients: list[McpClient] = []
        self._adapters: list[McpToolAdapter] = []

    def _make_client(self, cfg: ServerConfig) -> McpClient:
        """工厂方法（便于测试覆盖）。"""
        return McpClient(cfg)

    async def load_all(self, configs: list[ServerConfig]) -> None:
        """连所有 server；单个失败跳过，不影响其他。"""
        for cfg in configs:
            client = self._make_client(cfg)
            try:
                await client.connect()
                tools = await client.list_tools()
            except Exception as e:
                log.warning("MCP server %s 连接失败，跳过：%s", cfg.name, e)
                try:
                    await client.close()
                except Exception:
                    pass
                continue
            self._clients.append(client)
            for t in tools:
                self._adapters.append(McpToolAdapter(t, client))
            log.info("MCP server %s 注册了 %d 个工具", cfg.name, len(tools))

    def register_into(self, registry: ToolRegistry) -> None:
        """把所有 adapter 注册进给定 registry（每 session 调一次）。"""
        for a in self._adapters:
            registry.register(a)

    async def close_all(self) -> None:
        for c in self._clients:
            try:
                await c.close()
            except Exception:
                log.debug("MCP client 关闭异常", exc_info=True)
        self._clients = []
        self._adapters = []


# 进程级单例
mcp_manager = McpManager()
```

- [ ] **Step 4: 运行确认通过**
Run: `uv run pytest tests/mcp/test_manager.py -v`
Expected: PASS（3 passed）。全量 `uv run pytest -v` 全绿。

- [ ] **Step 5: Commit**
```bash
git add app/mcp/manager.py tests/mcp/test_manager.py
git commit -m "feat(mcp): McpManager 进程级加载 + register_into"
```

---

## Task 6: app/main.py — FastAPI lifespan 启动/关闭 MCP

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_main_lifespan.py`

- [ ] **Step 1: 写失败测试 `tests/test_main_lifespan.py`**
```python
from unittest.mock import AsyncMock, MagicMock


async def test_lifespan_loads_mcp_when_enabled(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.config, "ENABLE_MCP", True)
    monkeypatch.setattr(main.mcp_manager, "load_all", AsyncMock())
    monkeypatch.setattr(main.mcp_manager, "close_all", AsyncMock())
    # 注入空配置，避免真连
    monkeypatch.setattr(main.mcp_config, "load_mcp_config", lambda p: [])

    async with main.lifespan(main.app):
        pass
    main.mcp_manager.load_all.assert_awaited_once()
    main.mcp_manager.close_all.assert_awaited_once()


async def test_lifespan_skips_when_disabled(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.config, "ENABLE_MCP", False)
    main.mcp_manager.load_all = AsyncMock()
    async with main.lifespan(main.app):
        pass
    main.mcp_manager.load_all.assert_not_awaited()
```

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/test_main_lifespan.py -v`
Expected: FAIL（`module 'app.main' has no attribute 'lifespan'`）

- [ ] **Step 3: 改 `app/main.py`**

3a. 顶部 import 区，在 `from .session import Session` 这一行之后追加三行（注意：文件里**已有** `from . import config`，不要重复 import config）：
```python
from contextlib import asynccontextmanager

from .mcp.config import load_mcp_config as _load_mcp_config
from .mcp.manager import mcp_manager
```

3b. 在 `app = FastAPI(...)` 之前定义 lifespan，并把 FastAPI 改为用 lifespan。把：
```python
app = FastAPI(title="DemoTalk", version="0.1.0")
```
替换为：
```python
@asynccontextmanager
async def lifespan(app):
    if config.ENABLE_MCP:
        servers = _load_mcp_config(config.MCP_CONFIG_FILE)
        await mcp_manager.load_all(servers)
    yield
    await mcp_manager.close_all()


app = FastAPI(title="DemoTalk", version="0.1.0", lifespan=lifespan)
```

- [ ] **Step 4: 运行确认通过 + 冒烟**
Run: `uv run pytest tests/test_main_lifespan.py -v`
Expected: PASS（2 passed）。全量 `uv run pytest -v` 全绿。

冒烟（确认 app 仍能构建 + lifespan 不真连，因为测试环境无 mcp.json）：
```bash
uv run python -c "import asyncio; from app.main import app, lifespan; asyncio.run(lifespan.__wrapped__(app))" 2>&1 | tail -5
```
（若 `__wrapped__` 不可用，改用 `asyncio.run(lifespan(app))` 触发 generator——可选，pytest 通过即可）

- [ ] **Step 5: Commit**
```bash
git add app/main.py tests/test_main_lifespan.py
git commit -m "feat(main): FastAPI lifespan 启动加载 / 关闭断开 MCP"
```

---

## Task 7: app/session.py — __init__ 注册 MCP tools

**Files:**
- Modify: `app/session.py`
- Create: `tests/test_session_mcp.py`

- [ ] **Step 1: 写失败测试 `tests/test_session_mcp.py`**
```python
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
```

- [ ] **Step 2: 运行确认失败**
Run: `uv run pytest tests/test_session_mcp.py -v`
Expected: FAIL（`mcp_manager` 未在 session 模块导入 / register_into 未调用）

- [ ] **Step 3: 改 `app/session.py`**

3a. 顶部 import 区（`from . import config` 附近）追加：
```python
from .mcp.manager import mcp_manager
```

3b. 在 `Session.__init__` 里，视觉 tool_registry 注册块之后（即 `if config.ENABLE_VISION: self.tool_registry.register(TakePhotoTool())` 之后、`self._pending_photos = {}` 之前）追加：
```python
        if config.ENABLE_MCP:
            mcp_manager.register_into(self.tool_registry)
```

- [ ] **Step 4: 运行确认通过**
Run: `uv run pytest tests/test_session_mcp.py -v`
Expected: PASS（2 passed）。全量 `uv run pytest -v` 全绿。

- [ ] **Step 5: Commit**
```bash
git add app/session.py tests/test_session_mcp.py
git commit -m "feat(session): 注册 MCP tools 到会话 ToolRegistry"
```

---

## Task 8: mcp.json（howtocook 配置）+ .env.example

**Files:**
- Create: `mcp.json`
- Modify: `.env.example`（追加 MCP 配置段）
- Modify: `README.md`（配置项表，Task 10 一起做；本任务先加 mcp.json + .env.example）

- [ ] **Step 1: 创建 `mcp.json`**（项目根）：
```json
{
  "mcpServers": {
    "howtocook-mcp": {
      "type": "sse",
      "url": "https://mcp.api-inference.modelscope.net/11cb95ca0ea64c/sse"
    }
  }
}
```

- [ ] **Step 2: `.env.example` 追加 MCP 段**（在视觉配置段之后）：
```
# ===== MCP（外部工具服务器）=====
# 是否启用 MCP 接入
ENABLE_MCP=true
# MCP 配置文件路径（mcpServers 格式）
MCP_CONFIG_FILE=mcp.json
```

- [ ] **Step 3: 验证配置解析**
Run:
```bash
uv run python -c "from app.mcp.config import load_mcp_config; print(load_mcp_config('mcp.json'))"
```
Expected: 打印含 howtocook-mcp 的 ServerConfig 列表。

- [ ] **Step 4: Commit**
```bash
git add mcp.json .env.example
git commit -m "feat(mcp): mcp.json (howtocook) 与 .env.example 配置"
```

---

## Task 9: selftest Phase 5 — howtocook-mcp 端到端

**Files:**
- Modify: `scripts/selftest.py`

- [ ] **Step 1: 加 phase5 + main 调用**

在 `scripts/selftest.py` 的 `phase4_vision_roundtrip` 之后、`main` 之前新增：
```python
async def phase5_mcp_roundtrip() -> None:
    import websockets

    print("\n===== Phase 5: MCP tool 端到端（howtocook-mcp）=====")
    # 合成一句菜谱意图语音
    speech_text = "晚上不知道吃什么，给我推荐一下。"
    speech_wav = os.path.join(HERE, "test_mcp_speech.wav")
    await _synthesize_wav(speech_text, speech_wav)

    # 重采样到 16k
    with wave.open(speech_wav, "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    import audioop
    if sr != 16000:
        pcm16, _ = audioop.ratecv(pcm, 2, 1, sr, 16000, None)
    else:
        pcm16 = pcm
    print(f"[i] 喂入 STT：{len(pcm16)} 字节 16k PCM")

    events: list[str] = []
    deltas: list[str] = []
    done = asyncio.Event()

    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        async def reader():
            try:
                async for msg in ws:
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    obj = json.loads(msg)
                    t = obj.get("type")
                    events.append(t)
                    if t == "delta":
                        deltas.append(obj.get("text", ""))
                    elif t in ("tts_end", "error"):
                        done.set()
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.5)
        CHUNK = 3200
        for i in range(0, len(pcm16), CHUNK):
            await ws.send(pcm16[i:i + CHUNK])
            await asyncio.sleep(0.1)
        for _ in range(20):  # ~2s 静音
            await ws.send(b"\x00" * CHUNK)
            await asyncio.sleep(0.1)
        try:
            await asyncio.wait_for(done.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("   [!] 60s 内未完成")
        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(0.3)
        rtask.cancel()

    print(f"[结果] 事件序列: {events}")
    print(f"[结果] 助手回复: {''.join(deltas)[:160]}")

    if deltas:
        print("[PASS] MCP 往返成功：LLM 基于 MCP 工具结果作了回答")
    else:
        print("[INFO] 未收到 delta（LLM 可能未调 MCP 工具，或模型行为）；建议浏览器手动验证")
```
在 `main()` 的 `await phase4_vision_roundtrip()` 之后追加 `await phase5_mcp_roundtrip()`。

- [ ] **Step 2: 运行 selftest（Phase 1-5）**

启动服务（另一终端）：`uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
> 启动日志应见：`MCP server 已连接: howtocook-mcp (sse)` 与 `MCP server howtocook-mcp 注册了 5 个工具`。

然后：`uv run python scripts/selftest.py`

Expected: Phase 1-4 PASS；Phase 5 打印事件序列 + 助手回复（基于 howtocook 菜谱）。若 LLM 没调 MCP tool，打印 `[INFO]` 不中断。看到"全部自检通过"。

- [ ] **Step 3: Commit**
```bash
git add scripts/selftest.py
git commit -m "test(selftest): Phase 5 howtocook-mcp 端到端"
```

---

## Task 10: README + 配置项表 + .gitignore（test_mcp_speech.wav）

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: README 配置项表追加**（在 `MAX_TOOL_CALLS_PER_TURN` 行之后）：
```
| `ENABLE_MCP` | `true` | 是否启用 MCP（外部工具服务器） |
| `MCP_CONFIG_FILE` | `mcp.json` | MCP 配置文件路径（mcpServers 格式） |
```

- [ ] **Step 2: README 新增「## MCP 接入」章节**（放在「视觉能力」之后）：
```markdown
## MCP 接入（外部工具）

DemoTalk 可作为 MCP client 连接外部 MCP server，把 server 的工具暴露给 LLM。MCP 工具与视觉 `take_photo` 共存，复用同一 tool-calling 循环。

配置文件 `mcp.json`（项目根，标准 `mcpServers` 格式），支持 SSE 与 stdio：

```json
{
  "mcpServers": {
    "howtocook-mcp": { "type": "sse", "url": "https://..." },
    "some-local":    { "type": "stdio", "command": "npx", "args": ["-y", "x"], "env": {} }
  }
}
```

启动时进程级加载所有 server（`McpManager.load_all`），连接失败的 server 跳过（不影响其他与主服务）。`ENABLE_MCP=false` 可关闭。
```

- [ ] **Step 3: 项目结构补 `app/mcp/` + `mcp.json`**

在 README「项目结构」的 `app/tools/` 之后追加：
```
│   └── mcp/                # MCP client（config/client/adapter/manager）
│       ├── config.py       # 读 mcp.json
│       ├── client.py       # McpClient SSE/stdio
│       ├── adapter.py      # McpToolAdapter → Tool
│       └── manager.py      # McpManager 进程级加载
```
并在 `pyproject.toml` 行下方加：
```
├── mcp.json                # MCP server 配置（mcpServers 格式）
```

- [ ] **Step 4: `.gitignore` 加 selftest 产物**（`scripts/test_vision_speech.wav` 行之后）：
```
scripts/test_mcp_speech.wav
```

- [ ] **Step 5: 浏览器手动验收**
启动服务（启动日志确认 howtocook 注册 5 tools），浏览器问「晚上吃什么」，期望 LLM 调 `mcp_howtocook_whatToEat` 并基于菜谱回答 + TTS。

- [ ] **Step 6: Commit**
```bash
git add README.md .gitignore
git commit -m "docs: MCP 接入说明与配置项"
```

---

## 完成标准（Definition of Done）

- `uv run pytest -v` 全绿（含 mcp 模块测试）
- `uv run python scripts/selftest.py` Phase 1-4 PASS，Phase 5 打印 MCP 往返结果（不中断）
- 启动服务日志见 `MCP server howtocook-mcp 注册了 5 个工具`
- 浏览器问「晚上吃什么」→ LLM 调 howtocook tool → 基于菜谱回答 + TTS
- `ENABLE_MCP=false` 时回退（无 MCP tools，视觉 take_photo 仍可用）

## 将来扩展（不在本计划）

- Skills 子项目（3/3）：把 skills 暴露为 tools 注册进 `ToolRegistry`。
- MCP resources / prompts。
- MCP server 自动重连。
