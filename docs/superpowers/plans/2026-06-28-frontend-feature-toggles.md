# 前端功能开关（中断 / MCP / 语义结束）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前端增加三个开关（中断 / MCP / 语义结束），运行时可切换、立即生效，`.env` 提供默认值，localStorage 记住用户选择。

**Architecture:** 复用现有 WebSocket。后端会话初始化仍读 `.env`；连接后下发 `config_defaults`；前端据此初始化 toggle（localStorage 优先）并回发 `set_flags` 同步；切换时再发 `set_flags`。后端把 `barge_in` 改为会话属性、`mcp`/`end_by_voice` 通过 `tool_registry` 的来源分组动态增删，下一句/下一轮生效。

**Tech Stack:** Python 3.12 / FastAPI / asyncio（后端）；原生 JS + Web Audio + WebSocket（前端）；pytest + pytest-asyncio（asyncio_mode=auto）。

**设计文档：** `docs/superpowers/specs/2026-06-28-frontend-feature-toggles-design.md`

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `app/tools/registry.py` | 工具注册表：来源分组 + 增删 | 改造 |
| `app/mcp/manager.py` | MCP 进程级管理 | 标 source + `has_tools()` |
| `app/session.py` | 会话编排 | `barge_in` 属性 + `config_defaults` 下发 + `set_flags` |
| `app/main.py` | WS 入口 | 分发 `set_flags` |
| `static/index.html` | 界面结构 | 齿轮 + 设置面板 |
| `static/style.css` | 样式 | 面板 + 开关（暖夜风格） |
| `static/app.js` | 前端逻辑 | flags + localStorage + 同步 |
| `tests/tools/test_registry.py` | registry 测试 | 新增 |
| `tests/mcp/test_manager.py` | manager 测试 | 新增 |
| `tests/test_session_flags.py` | session 开关测试 | 新增 |
| `README.md` | 文档 | 补充三开关说明 |

---

## Task 1: ToolRegistry 来源分组 + 增删能力

**Files:**
- Modify: `app/tools/registry.py`
- Test: `tests/tools/test_registry.py`

为动态屏蔽 MCP 工具、增删 `end_conversation`，registry 需记录每个工具的来源（builtin/mcp），并支持按来源批量移除、按名移除。

- [ ] **Step 1: 写失败测试**

Create `tests/tools/test_registry.py`:

```python
import pytest
from app.tools.registry import ToolRegistry
from app.tools.base import Tool, ToolContext, ToolResult


class _FakeTool(Tool):
    def __init__(self, name: str):
        self._schema = {
            "name": name,
            "description": "fake",
            "parameters": {"type": "object", "properties": {}},
        }

    @property
    def schema(self) -> dict:
        return self._schema

    async def execute(self, ctx: ToolContext) -> ToolResult:
        return ToolResult(text=f"exec:{self._schema['name']}")


async def _noop_photo(_call_id: str) -> str | None:
    return None


def test_register_default_source():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    assert r.get("a") is not None
    assert r.sources() == {"a": "builtin"}


def test_register_with_source():
    r = ToolRegistry()
    r.register(_FakeTool("mcp_x"), source="mcp")
    assert r.sources() == {"mcp_x": "mcp"}


def test_unregister_removes_tool():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    r.unregister("a")
    assert r.get("a") is None
    assert r.schemas() == []


def test_unregister_missing_is_noop():
    r = ToolRegistry()
    r.unregister("nope")  # 不应抛异常


def test_clear_by_source_removes_only_that_source():
    r = ToolRegistry()
    r.register(_FakeTool("builtin1"))
    r.register(_FakeTool("mcp_a"), source="mcp")
    r.register(_FakeTool("mcp_b"), source="mcp")
    r.clear_by_source("mcp")
    assert set(r.sources()) == {"builtin1"}


def test_clear_by_source_keeps_builtin():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    r.register(_FakeTool("b"), source="mcp")
    r.clear_by_source("mcp")
    assert set(r.sources()) == {"a"}


def test_schemas_format():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    assert r.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "a",
                "description": "fake",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def test_execute_registered_tool():
    r = ToolRegistry()
    r.register(_FakeTool("a"))
    ctx = ToolContext(call_id="1", args={}, request_photo=_noop_photo)
    result = await r.execute("a", ctx)
    assert result.text == "exec:a"


async def test_execute_unknown_tool():
    r = ToolRegistry()
    ctx = ToolContext(call_id="1", args={}, request_photo=_noop_photo)
    result = await r.execute("missing", ctx)
    assert "未知工具" in result.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/tools/test_registry.py -v`
Expected: FAIL（`sources` / `unregister` / `clear_by_source` 不存在；`register` 不接受 `source`）

- [ ] **Step 3: 改造 registry**

Replace `app/tools/registry.py` 全文为:

```python
"""工具注册表：管理内置/外部工具，提供 schema 列表与统一执行入口。

工具带来源标记（source），便于按来源批量增删——例如会话级屏蔽 MCP 工具。
"""
from __future__ import annotations

import logging

from .base import Tool, ToolContext, ToolResult

log = logging.getLogger("demotalk.tools")


class ToolRegistry:
    def __init__(self) -> None:
        # name -> (tool, source)；source 如 "builtin" / "mcp"
        self._tools: dict[str, tuple[Tool, str]] = {}

    def register(self, tool: Tool, source: str = "builtin") -> None:
        self._tools[tool.schema["name"]] = (tool, source)

    def unregister(self, name: str) -> None:
        """按名移除单个工具；不存在则空操作。"""
        self._tools.pop(name, None)

    def clear_by_source(self, source: str) -> None:
        """移除指定来源的全部工具（如屏蔽所有 MCP 工具）。"""
        self._tools = {n: v for n, v in self._tools.items() if v[1] != source}

    def get(self, name: str) -> Tool | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def sources(self) -> dict[str, str]:
        """name -> source 映射，供判断某来源是否存在工具。"""
        return {n: s for n, (_, s) in self._tools.items()}

    def schemas(self) -> list[dict]:
        """OpenAI chat completions 的 tools 参数格式。"""
        return [{"type": "function", "function": t.schema} for (t, _s) in self._tools.values()]

    async def execute(self, name: str, ctx: ToolContext) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(text=f"未知工具：{name}")
        try:
            return await tool.execute(ctx)
        except Exception as e:
            log.exception("工具 %s 执行异常", name)
            return ToolResult(text=f"工具执行出错：{e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/tools/test_registry.py -v`
Expected: 9 passed

- [ ] **Step 5: 跑全量回归**

Run: `uv run pytest -q`
Expected: 全绿（registry 改造向后兼容：现有 `register(tool)` 调用走默认 source="builtin"）

- [ ] **Step 6: 提交**

```bash
git add app/tools/registry.py tests/tools/test_registry.py
git commit -m "$(cat <<'EOF'
refactor(tools): registry 加来源标记与增删能力

- _tools 存 (tool, source)；register 默认 source="builtin"
- 新增 unregister(name)、clear_by_source(source)、sources()
- 为会话级动态屏蔽 MCP 工具、增删 end_conversation 做准备

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: McpManager 标 source + has_tools()

**Files:**
- Modify: `app/mcp/manager.py`
- Test: `tests/mcp/test_manager.py`

`register_into` 注册时要标 `source="mcp"`，并暴露 `has_tools()` 供后端下发 `mcp_available`。

- [ ] **Step 1: 写失败测试**

Create `tests/mcp/test_manager.py`:

```python
from app.mcp.manager import McpManager
from app.tools.registry import ToolRegistry


class _FakeAdapter:
    """模拟 McpToolAdapter：registry 只用到 .schema['name']。"""

    def __init__(self, name: str):
        self.schema = {
            "name": name,
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/mcp/test_manager.py -v`
Expected: FAIL（`has_tools` 不存在；`register_into` 未标 source，`sources()` 全为 "builtin"）

- [ ] **Step 3: 改造 manager**

Modify `app/mcp/manager.py`，把 `register_into` 方法替换为:

```python
    def register_into(self, registry: ToolRegistry) -> None:
        """把所有 adapter 注册进给定 registry（每 session 调一次），标记来源 mcp。

        命名：MCP tool 用其原始 name（通常已带 server 前缀，如 mcp_howtocook_*），
        调用方需保证跨 server / 与 take_photo 唯一；本方法不做冲突降级。
        """
        for a in self._adapters:
            registry.register(a, source="mcp")

    def has_tools(self) -> bool:
        """是否加载到了可用 MCP 工具（供下发 mcp_available）。"""
        return len(self._adapters) > 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/mcp/test_manager.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add app/mcp/manager.py tests/mcp/test_manager.py
git commit -m "$(cat <<'EOF'
feat(mcp): register_into 标 source=mcp + has_tools()

为会话级屏蔽 MCP 工具与下发 mcp_available 做准备。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Session 会话级开关 + main 分发 set_flags

**Files:**
- Modify: `app/session.py`
- Modify: `app/main.py`
- Test: `tests/test_session_flags.py`

把 `barge_in` 从全局配置改为会话属性；`start()` 下发 `config_defaults`；新增 `set_flags(msg)` 动态应用三个开关；`main.py` 分发 `set_flags`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_session_flags.py`:

```python
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
        self.schema = {"name": name, "description": "mcp", "parameters": {"type": "object", "properties": {}}}


# ---- barge_in 会话属性 ----

def test_barge_in_initialized_from_config():
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


# ---- config_defaults 下发 ----

async def test_start_emits_config_defaults(monkeypatch):
    s, ws = _make_session()
    monkeypatch.setattr(s.stt, "start", lambda: None)  # 避免连 STT SDK
    await s.start()
    objs = _sent_objects(ws)
    cd = next((o for o in objs if o.get("type") == "config_defaults"), None)
    assert cd is not None
    assert cd["barge_in"] == config.ENABLE_BARGE_IN
    assert cd["mcp"] == config.ENABLE_MCP
    assert cd["end_by_voice"] == config.ENABLE_END_BY_VOICE
    assert "mcp_available" in cd
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_session_flags.py -v`
Expected: FAIL（`barge_in_enabled` 属性不存在、`set_flags` 不存在、`config_defaults` 未下发）

- [ ] **Step 3: session.py 加 barge_in 属性**

Modify `app/session.py` 的 `__init__`，在 `self._running = True` 之后加一行:

```python
        self._running = True
        # barge-in：会话级开关（默认取 .env，前端 set_flags 可动态覆盖）
        self.barge_in_enabled = config.ENABLE_BARGE_IN
```

- [ ] **Step 4: _on_final 改读会话属性**

Modify `app/session.py` 的 `_on_final`，把:

```python
        if self.state == "speaking":
            if config.ENABLE_BARGE_IN:
                await self._barge_in()
            else:
                # 不打断：忽略说话期间的输入
                return
```

替换为:

```python
        if self.state == "speaking":
            if self.barge_in_enabled:
                await self._barge_in()
            else:
                # 不打断：忽略说话期间的输入
                return
```

- [ ] **Step 5: start() 下发 config_defaults**

Modify `app/session.py` 的 `start()`，在 `vision_config` 下发块之后、`await self._set_state("listening")` 之前插入:

```python
        # 下发三开关的 .env 默认值 + MCP 可用性，供前端初始化 toggle
        await self._send(
            {
                "type": "config_defaults",
                "barge_in": config.ENABLE_BARGE_IN,
                "mcp": config.ENABLE_MCP,
                "end_by_voice": config.ENABLE_END_BY_VOICE,
                "mcp_available": mcp_manager.has_tools(),
            }
        )
```

- [ ] **Step 6: 新增 set_flags 方法**

Modify `app/session.py`，在 `handle_control` 方法之后新增:

```python
    async def set_flags(self, msg: dict) -> None:
        """前端 set_flags：动态应用 barge_in / mcp / end_by_voice 三个开关。

        - barge_in：改会话属性，下一句句末生效
        - mcp：会话级屏蔽/恢复 MCP 工具（连接保持），下一轮 LLM 调用生效
        - end_by_voice：增删 end_conversation 工具，下一轮 LLM 调用生效
        每个字段用 `if key in msg` 守卫，部分/重复均幂等。
        """
        if "barge_in" in msg:
            self.barge_in_enabled = bool(msg["barge_in"])

        if "end_by_voice" in msg:
            want = bool(msg["end_by_voice"])
            has = self.tool_registry.get("end_conversation") is not None
            if want and not has:
                from .tools.builtin.end_conversation import EndConversationTool
                self.tool_registry.register(EndConversationTool())
            elif not want and has:
                self.tool_registry.unregister("end_conversation")

        if "mcp" in msg:
            want = bool(msg["mcp"])
            has_mcp = any(src == "mcp" for src in self.tool_registry.sources().values())
            if want and not has_mcp:
                mcp_manager.register_into(self.tool_registry)
            elif not want and has_mcp:
                self.tool_registry.clear_by_source("mcp")
```

- [ ] **Step 7: main.py 分发 set_flags**

Modify `app/main.py` 的 `ws_endpoint` 消息分发，把:

```python
                elif t == "photo_error":
                    await session.handle_photo_error(obj.get("call_id", ""))
                else:
                    await session.handle_control(obj)
```

替换为:

```python
                elif t == "photo_error":
                    await session.handle_photo_error(obj.get("call_id", ""))
                elif t == "set_flags":
                    await session.set_flags(obj)
                else:
                    await session.handle_control(obj)
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest tests/test_session_flags.py -v`
Expected: 9 passed

- [ ] **Step 9: 跑全量回归**

Run: `uv run pytest -q`
Expected: 全绿

- [ ] **Step 10: 提交**

```bash
git add app/session.py app/main.py tests/test_session_flags.py
git commit -m "$(cat <<'EOF'
feat(session): 会话级功能开关 + config_defaults 下发

- barge_in 改会话属性 self.barge_in_enabled（_on_final 读属性）
- start() 下发 config_defaults（三开关 .env 值 + mcp_available）
- 新增 set_flags(msg)：动态改 barge_in / 增删 mcp 与 end_conversation 工具
- main.py ws_endpoint 分发 set_flags

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 前端设置面板 + toggle 同步逻辑

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `static/app.js`

前端无自动化测试框架，采用「实现 + 手动验证」。新增齿轮按钮、设置面板、三个 toggle，以及 flags 状态 + localStorage + `config_defaults`/`set_flags` 同步逻辑。不动现有 WS/音频/打字机代码。

- [ ] **Step 1: index.html 加齿轮 + 设置面板**

Modify `static/index.html`，把顶栏 `<div class="status">` 替换为（齿轮在 pills 左侧）:

```html
      <div class="status">
        <button id="btnSettings" class="icon-btn" aria-label="设置" title="设置">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path fill="currentColor" d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm9.3 3.2-2.1-.5a7.8 7.8 0 0 0-.5-1.3l1.1-1.8a.6.6 0 0 0-.1-.8l-1.7-1.7a.6.6 0 0 0-.8-.1l-1.8 1.1a7.8 7.8 0 0 0-1.3-.5l-.5-2.1a.6.6 0 0 0-.6-.5h-2.4a.6.6 0 0 0-.6.5l-.5 2.1a7.8 7.8 0 0 0-1.3.5L7.7 4.3a.6.6 0 0 0-.8.1L5.2 6.1a.6.6 0 0 0-.1.8l1.1 1.8a7.8 7.8 0 0 0-.5 1.3l-2.1.5a.6.6 0 0 0-.5.6v2.4c0 .3.2.5.5.6l2.1.5c.1.5.3.9.5 1.3l-1.1 1.8c-.2.3-.1.6.1.8l1.7 1.7c.2.2.5.2.8.1l1.8-1.1c.4.2.8.4 1.3.5l.5 2.1c.1.3.3.5.6.5h2.4c.3 0 .5-.2.6-.5l.5-2.1c.5-.1.9-.3 1.3-.5l1.8 1.1c.3.2.6.1.8-.1l1.7-1.7c.2-.2.2-.5.1-.8l-1.1-1.8c.2-.4.4-.8.5-1.3l2.1-.5a.6.6 0 0 0 .5-.6v-2.4a.6.6 0 0 0-.4-.6z"/>
          </svg>
        </button>
        <span id="connPill" class="pill pill-muted">未连接</span>
        <span id="statePill" class="pill">待机</span>
      </div>
```

在 `</footer>` 之后、`<div id="flash"></div>` 之前插入设置面板:

```html
    <div id="settingsPanel" class="settings-panel hidden" role="dialog" aria-label="设置">
      <div class="settings-head">
        <span>设置</span>
        <button id="btnCloseSettings" class="icon-btn sm" aria-label="关闭">×</button>
      </div>
      <div class="toggle-row" data-flag="barge_in">
        <div class="toggle-info">
          <div class="toggle-label">允许打断</div>
          <div class="toggle-desc">助手说话时你开口，立即停止并进入新一轮</div>
        </div>
        <button class="switch" role="switch" aria-checked="true"></button>
      </div>
      <div class="toggle-row" data-flag="mcp">
        <div class="toggle-info">
          <div class="toggle-label">MCP 工具</div>
          <div class="toggle-desc">允许助手调用外部工具服务器</div>
        </div>
        <button class="switch" role="switch" aria-checked="true"></button>
      </div>
      <div class="toggle-row" data-flag="end_by_voice">
        <div class="toggle-info">
          <div class="toggle-label">语义结束</div>
          <div class="toggle-desc">说「再见」等，由助手自动结束对话</div>
        </div>
        <button class="switch" role="switch" aria-checked="true"></button>
      </div>
      <div class="settings-foot">默认值来自 .env，切换后立即生效</div>
    </div>
```

- [ ] **Step 2: style.css 加面板与开关样式**

在 `static/style.css` 末尾（响应式媒体查询之前）追加:

```css
/* ---- 设置入口与面板 ---- */
.icon-btn {
  width: 34px; height: 34px;
  display: grid; place-items: center;
  border-radius: 10px;
  background: var(--glass);
  border: 1px solid var(--brd);
  color: var(--muted);
  cursor: pointer;
  transition: color .2s, background .2s, border-color .2s, transform .08s;
}
.icon-btn:hover { color: var(--amber); border-color: rgba(245,166,83,.4); background: rgba(245,166,83,.08); }
.icon-btn:active { transform: translateY(1px); }
.icon-btn.sm { width: 26px; height: 26px; font-size: 18px; line-height: 1; }

.settings-panel {
  position: absolute;
  top: 64px; right: 24px;
  width: 300px;
  padding: 14px;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(25,34,58,.96), rgba(18,26,44,.96));
  border: 1px solid var(--brd-strong);
  backdrop-filter: blur(18px) saturate(125%);
  -webkit-backdrop-filter: blur(18px) saturate(125%);
  box-shadow: 0 18px 50px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.06);
  z-index: 30;
  animation: pop .18s cubic-bezier(.2,.8,.25,1);
}
.settings-panel.hidden { display: none; }
.settings-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 14px; font-weight: 600; color: var(--text-warm);
  margin-bottom: 10px;
}
.toggle-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding: 9px 4px;
  border-top: 1px solid var(--brd);
}
.toggle-info { min-width: 0; }
.toggle-label { font-size: 13.5px; color: var(--text); }
.toggle-desc { font-size: 11.5px; color: var(--muted); line-height: 1.5; margin-top: 2px; }
.settings-foot { margin-top: 10px; font-size: 11px; color: var(--muted); text-align: center; }

/* 开关 */
.switch {
  flex: none;
  width: 40px; height: 22px;
  border-radius: 999px;
  border: 1px solid var(--brd-strong);
  background: rgba(255,255,255,.08);
  position: relative;
  cursor: pointer;
  padding: 0;
  transition: background .2s, border-color .2s;
}
.switch::after {
  content: "";
  position: absolute; top: 2px; left: 2px;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: #cfd5e4;
  transition: transform .2s cubic-bezier(.2,.8,.25,1), background .2s;
}
.switch[aria-checked="true"] {
  background: linear-gradient(135deg, var(--amber-bright), var(--amber-deep));
  border-color: rgba(245,166,83,.5);
  box-shadow: 0 0 12px rgba(245,166,83,.3);
}
.switch[aria-checked="true"]::after { transform: translateX(18px); background: #1c130a; }
.switch:disabled { opacity: .4; cursor: not-allowed; }
```

- [ ] **Step 3: app.js 加 flags 状态与 localStorage**

Modify `static/app.js`，在 `let endingByVoice = false;` 之后加:

```javascript
// ---- 功能开关（中断 / MCP / 语义结束）----
const FLAG_KEYS = ["barge_in", "mcp", "end_by_voice"];
const LS_KEY = "demotalk.flags";
let flags = { barge_in: true, mcp: true, end_by_voice: true };
let mcpAvailable = true;

function loadFlags() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    for (const k of FLAG_KEYS) if (typeof saved[k] === "boolean") flags[k] = saved[k];
  } catch (e) { /* 损坏则忽略，用默认 */ }
}
function saveFlags() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(flags)); } catch (e) {}
}
```

- [ ] **Step 4: app.js 加 DOM 引用与渲染**

在 app.js 顶部 `// ---- DOM ----` 区，已有引用之后加:

```javascript
const btnSettings = $("#btnSettings");
const btnCloseSettings = $("#btnCloseSettings");
const settingsPanel = $("#settingsPanel");
const toggleRows = settingsPanel.querySelectorAll(".toggle-row");
```

加渲染与同步函数（放在 `handleEvent` 函数之前）:

```javascript
function renderToggles() {
  toggleRows.forEach((row) => {
    const key = row.dataset.flag;
    const sw = row.querySelector(".switch");
    sw.setAttribute("aria-checked", String(flags[key]));
    sw.disabled = key === "mcp" && !mcpAvailable;
  });
}
function sendFlags() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: "set_flags", ...flags })); } catch (e) {}
  }
}
function setFlag(key, val) {
  flags[key] = val;
  saveFlags();
  renderToggles();
  sendFlags();
}
function applyDefaults(defaults) {
  // localStorage 上次值 > .env 默认
  loadFlags();
  if (typeof defaults.barge_in === "boolean" && !(LS_KEY in localStorage)) flags.barge_in = defaults.barge_in;
  if (typeof defaults.mcp === "boolean" && !(LS_KEY in localStorage)) flags.mcp = defaults.mcp;
  if (typeof defaults.end_by_voice === "boolean" && !(LS_KEY in localStorage)) flags.end_by_voice = defaults.end_by_voice;
  mcpAvailable = defaults.mcp_available !== false;
  renderToggles();
  sendFlags(); // 连接后立即把会话对齐到用户选择
}
```

- [ ] **Step 5: app.js handleEvent 处理 config_defaults**

Modify `static/app.js` 的 `handleEvent` switch，在 `case "vision_config":` 之后加:

```javascript
    case "config_defaults":
      applyDefaults(obj);
      break;
```

- [ ] **Step 6: app.js 绑定交互**

在 app.js 末尾（`setState("idle");` 之后）加:

```javascript
// ---- 设置面板交互 ----
function toggleSettings(open) {
  settingsPanel.classList.toggle("hidden", !open);
}
btnSettings.addEventListener("click", (e) => { e.stopPropagation(); toggleSettings(settingsPanel.classList.contains("hidden")); });
btnCloseSettings.addEventListener("click", () => toggleSettings(false));
document.addEventListener("click", (e) => {
  if (!settingsPanel.classList.contains("hidden") && !settingsPanel.contains(e.target) && e.target !== btnSettings) {
    toggleSettings(false);
  }
});
toggleRows.forEach((row) => {
  row.querySelector(".switch").addEventListener("click", (e) => {
    e.stopPropagation();
    const sw = e.currentTarget;
    if (sw.disabled) return;
    setFlag(row.dataset.flag, !(sw.getAttribute("aria-checked") === "true"));
  });
});

// 初始渲染（未连接时也显示开关，供用户预先设置）
loadFlags();
renderToggles();
```

- [ ] **Step 7: 手动验证**

启动后端 `uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，浏览器打开 http://127.0.0.1:8000 ，逐项验证：

1. 右上齿轮可见，点击弹出设置面板，三个开关初始 = `.env` 默认（首次无 localStorage）
2. 点「开始对话」授权后说话；会话中关「允许打断」，助手说话时再开口 → 不再打断（验证 barge_in 立即生效）
3. 会话中关「MCP 工具」，再问需用 MCP 的问题 → 助手不再调用 MCP 工具（验证下一轮生效）
4. 会话中关「语义结束」，说「再见」→ 助手不再自动结束（验证 end_conversation 工具已移除）
5. 刷新页面 → 开关保持上次选择（localStorage）
6. 清除 localStorage（开发者工具）后刷新 → 开关回到 `.env` 默认
7. `.env` 设 `ENABLE_MCP=false` 重启 → MCP 开关 disabled

- [ ] **Step 8: 提交**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "$(cat <<'EOF'
feat(web): 设置面板 + 三开关（中断/MCP/语义结束）

- 顶栏齿轮弹出设置面板，三个暖琥珀 toggle
- flags 状态 + localStorage 记忆（上次值优先于 .env 默认）
- 收到 config_defaults 初始化 toggle 并回发 set_flags 同步
- 切换 toggle 立即发 set_flags，后端动态生效
- MCP 不可用时（.env 未启用）toggle disabled

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: README 补充三开关说明

**Files:**
- Modify: `README.md`

在「配置项」表后或「视觉能力」节附近，说明三开关现在可在前端运行时切换、`.env` 为默认值。

- [ ] **Step 1: 补充说明**

Modify `README.md`，在「## 配置项（.env）」节末尾（`HOST` / `PORT` 行之后）加一段小节:

```markdown

### 运行时开关（前端）

`ENABLE_BARGE_IN` / `ENABLE_MCP` / `ENABLE_END_BY_VOICE` 三项除 `.env` 默认值外，还可在浏览器右上角齿轮设置面板中**运行时切换**，立即生效（中断下一句句末生效；MCP / 语义结束下一轮 LLM 调用生效）。切换状态用 localStorage 记住，优先级：`localStorage 上次值` > `.env 默认`。MCP 仅屏蔽当前会话的工具暴露，不卸载连接。
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): 补充前端运行时开关说明

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## 完成标准

- 全部 pytest 通过：`uv run pytest -q`
- Task 4 手动验证 7 项全部符合预期
- 设计文档验收标准 1–6 全部满足
