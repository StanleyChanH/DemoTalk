# 语义结束对话（end_conversation 工具）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户用自然语言（「再见」「拜拜」「结束对话」等）即可结束对话，由 LLM 主动调用新增的 `end_conversation` 工具触发，告别语 TTS 播完后优雅断连——体验与点「结束对话」按钮一致。

**Architecture:** 复用现有 `app/tools/` tool-calling 框架（视觉 `take_photo`、MCP 同机制）。新增 `EndConversationTool`；`Session` 在 tool 执行时置 `_ending` 标志、跳出 LLM 循环，待 TTS 播完后发 `conversation_end` 事件；前端等播放队列空再 `stopSession`；后端 30s 兜底强制关闭 WS。

**Tech Stack:** Python 3.12 + FastAPI + asyncio + openai SDK（LLM tool-calling）；原生 JS 前端（WebSocket）；pytest + pytest-asyncio（asyncio_mode=auto）；uv 管理依赖与运行。

**Spec:** [docs/superpowers/specs/2026-06-27-voice-end-conversation-design.md](../specs/2026-06-27-voice-end-conversation-design.md)

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `app/config.py` | 改 | 新增 `ENABLE_END_BY_VOICE`；默认 `LLM_SYSTEM_PROMPT` 追加 `end_conversation` 引导 |
| `.env.example` | 改 | 新增 `ENABLE_END_BY_VOICE=true`；prompt 对齐 config 默认（补 take_photo + end_conversation 引导） |
| `app/tools/base.py` | 改 | `ToolContext` 新增可选 `request_end_conversation` 回调 |
| `app/tools/builtin/end_conversation.py` | 新增 | `EndConversationTool`：schema + execute（调回调） |
| `app/session.py` | 改 | 注册 tool；`_ending`/`_end_fallback` 标志；`request_end_conversation`；`_on_final` 守卫；`_run_turn` 结束分支；`_on_tts_state` 发 `conversation_end`；`_force_close_after` 兜底；`ToolContext` 补回调 |
| `static/app.js` | 改 | `endingByVoice` 标志；`conversation_end` 事件处理；`onended` 播完即关 |
| `tests/test_config.py` | 改 | `ENABLE_END_BY_VOICE` 默认值测试 |
| `tests/tools/test_base.py` | 改 | `ToolContext` 新字段测试 |
| `tests/tools/builtin/test_end_conversation.py` | 新增 | `EndConversationTool` 单测 |
| `tests/test_session_end.py` | 新增 | session 收尾单测 |
| `README.md` | 改 | 配置项表 + 协议事件说明 + 项目结构 |

---

## Task 1: config 开关 + system prompt 引导

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
def test_end_by_voice_defaults():
    assert config.ENABLE_END_BY_VOICE is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py::test_end_by_voice_defaults -v`
Expected: FAIL with `AttributeError: module 'app.config' has no attribute 'ENABLE_END_BY_VOICE'`

- [ ] **Step 3: 加 config 开关**

在 `app/config.py` 的 `# ---- 视觉 / tool-calling ----` 区块末尾（`MAX_TOOL_CALLS_PER_TURN` 那行之后）追加：

```python
# 是否启用「语义结束对话」（用户说再见等由 LLM 调 end_conversation 工具结束）
ENABLE_END_BY_VOICE: bool = _bool("ENABLE_END_BY_VOICE", True)
```

- [ ] **Step 4: 追加 system prompt 引导**

在 `app/config.py` 修改 `LLM_SYSTEM_PROMPT` 的默认值，把末句

```
...先调用 take_photo 拍照再回答。",
```

改为：

```python
        "你是一个简洁友好的中文语音助手。请用 1-2 句话简短回答，口语化、适合语音播报，不要使用 markdown 或列表。当需要看用户周围画面时（例如用户问『这是什么』『前面有什么』），先调用 take_photo 拍照再回答。当用户明确表达结束对话（如『再见／拜拜／结束对话／不聊了／先这样吧／挂了』）时，可先用一句话简短告别，并调用 end_conversation 工具结束对话；无明确结束意图时不要调用。",
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（全部 config 测试通过）

- [ ] **Step 6: 对齐 .env.example**

在 `.env.example` 的 `# ===== 视觉 / tool-calling =====` 区块末尾（`MAX_TOOL_CALLS_PER_TURN=3` 之后、`# ===== MCP` 之前）追加：

```
# 是否启用「语义结束对话」（说再见等由 LLM 调 end_conversation 结束）
ENABLE_END_BY_VOICE=true
```

同时把 `.env.example` 第 18 行的 `LLM_SYSTEM_PROMPT` 值对齐 config 默认（补上 take_photo + end_conversation 引导），改为：

```
LLM_SYSTEM_PROMPT=你是一个简洁友好的中文语音助手。请用 1-2 句话简短回答，口语化、适合语音播报，不要使用 markdown 或列表。当需要看用户周围画面时（例如用户问『这是什么』『前面有什么』），先调用 take_photo 拍照再回答。当用户明确表达结束对话（如『再见／拜拜／结束对话／不聊了／先这样吧／挂了』）时，可先用一句话简短告别，并调用 end_conversation 工具结束对话；无明确结束意图时不要调用。
```

- [ ] **Step 7: 提交**

```bash
git add app/config.py .env.example tests/test_config.py
git commit -m "feat(config): ENABLE_END_BY_VOICE 开关 + end_conversation prompt 引导"
```

---

## Task 2: ToolContext 扩展（request_end_conversation 回调）

**Files:**
- Modify: `app/tools/base.py`
- Test: `tests/tools/test_base.py`

- [ ] **Step 1: 写失败测试**

在 `tests/tools/test_base.py` 末尾追加：

```python
def test_tool_context_end_conversation_optional_default():
    # 不传 request_end_conversation 时默认 None（兼容 MCP 适配器等不使用的 tool）
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    assert ctx.request_end_conversation is None


async def test_tool_context_end_conversation_can_be_injected():
    async def fake_end():
        return None

    ctx = ToolContext(
        call_id="c1",
        args={},
        request_photo=lambda cid: None,
        request_end_conversation=fake_end,
    )
    assert callable(ctx.request_end_conversation)
    await ctx.request_end_conversation()  # 可 await
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/tools/test_base.py::test_tool_context_end_conversation_optional_default -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'request_end_conversation'`

- [ ] **Step 3: 给 ToolContext 加可选字段**

在 `app/tools/base.py` 把 `ToolContext` 改为：

```python
@dataclass
class ToolContext:
    """工具执行上下文。request_photo 由 session 注入（返回 data URL 或 None）。
    request_end_conversation 可选，由 session 注入（仅 end_conversation 工具使用）。"""

    call_id: str
    args: dict
    request_photo: Callable[[str], Awaitable[str | None]]
    request_end_conversation: Callable[[], Awaitable[None]] | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/tools/test_base.py -v`
Expected: PASS（全部 base 测试通过）

- [ ] **Step 5: 提交**

```bash
git add app/tools/base.py tests/tools/test_base.py
git commit -m "feat(tools): ToolContext 增 request_end_conversation 回调"
```

---

## Task 3: EndConversationTool

**Files:**
- Create: `app/tools/builtin/end_conversation.py`
- Test: `tests/tools/builtin/test_end_conversation.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/tools/builtin/test_end_conversation.py`：

```python
import pytest
from app.tools.base import ToolContext
from app.tools.builtin.end_conversation import EndConversationTool


@pytest.fixture
def tool():
    return EndConversationTool()


def test_schema(tool):
    s = tool.schema
    assert s["name"] == "end_conversation"
    assert s["parameters"] == {"type": "object", "properties": {}}
    assert "结束" in s["description"]


async def test_execute_invokes_request_end_conversation(tool):
    called = {"n": 0}

    async def fake_end():
        called["n"] += 1

    ctx = ToolContext(
        call_id="c1",
        args={},
        request_photo=lambda cid: None,
        request_end_conversation=fake_end,
    )
    result = await tool.execute(ctx)
    assert called["n"] == 1  # 回调被 await 调用
    assert isinstance(result.text, str)


async def test_execute_ok_when_no_callback(tool):
    # 兼容：ctx 未注入回调时不报错（防御）
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    result = await tool.execute(ctx)
    assert isinstance(result.text, str)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/tools/builtin/test_end_conversation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tools.builtin.end_conversation'`

- [ ] **Step 3: 实现 EndConversationTool**

创建 `app/tools/builtin/end_conversation.py`：

```python
"""内置工具：结束对话。用户明确表达结束意图时由 LLM 调用，
经 ToolContext.request_end_conversation 触发会话优雅关闭。"""
from __future__ import annotations

from ..base import ToolContext, ToolResult


class EndConversationTool:
    """用户明确要结束对话时调用，触发会话收尾（告别语播完后断连）。"""

    @property
    def schema(self) -> dict:
        return {
            "name": "end_conversation",
            "description": "当用户明确表达要结束对话时调用（如『再见』『拜拜』『结束对话』『不聊了』『先这样吧』『挂了』）。调用前可用一句话简短告别。",
            "parameters": {"type": "object", "properties": {}},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        if ctx.request_end_conversation:
            await ctx.request_end_conversation()
        return ToolResult(text="(对话已结束)")  # 仅留作历史；结束分支不再调 LLM
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/tools/builtin/test_end_conversation.py -v`
Expected: PASS（3 个测试全过）

- [ ] **Step 5: 提交**

```bash
git add app/tools/builtin/end_conversation.py tests/tools/builtin/test_end_conversation.py
git commit -m "feat(tools): EndConversationTool（LLM 语义结束对话）"
```

---

## Task 4: Session 收尾 —— _ending 标志 + _run_turn 跳出 + _on_final 守卫 + 注册

**Files:**
- Modify: `app/session.py`
- Test: `tests/test_session_end.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_end.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_session_end.py -v`
Expected: FAIL（`Session` 无 `_ending` / `request_end_conversation`；`_run_turn` 不识别 end_conversation）

- [ ] **Step 3: __init__ 注册 tool + 标志初始化**

在 `app/session.py` 顶部 import 区，把

```python
from .tools.builtin.take_photo import TakePhotoTool
```

改为（加一行 import）：

```python
from .tools.builtin.take_photo import TakePhotoTool
from .tools.builtin.end_conversation import EndConversationTool
```

在 `Session.__init__` 中，把 tool 注册块

```python
        self.tool_registry = ToolRegistry()
        if config.ENABLE_VISION:
            self.tool_registry.register(TakePhotoTool())
        if config.ENABLE_MCP:
            mcp_manager.register_into(self.tool_registry)
```

改为（追加 end_conversation 注册）：

```python
        self.tool_registry = ToolRegistry()
        if config.ENABLE_VISION:
            self.tool_registry.register(TakePhotoTool())
        if config.ENABLE_END_BY_VOICE:
            self.tool_registry.register(EndConversationTool())
        if config.ENABLE_MCP:
            mcp_manager.register_into(self.tool_registry)
```

在 `__init__` 末尾的状态初始化区（`self._turn_task: asyncio.Task | None = None` 之后）追加：

```python
        # 语义结束：end_conversation 工具触发后置 True，待 TTS 播完再断连
        self._ending = False
        # 兜底强制关闭 WS 的延时任务
        self._end_fallback: asyncio.Task | None = None
```

- [ ] **Step 4: 新增 request_end_conversation 方法**

在 `Session` 的「拍照（tool 交互）」区块之前（`_barge_in` 方法之后）插入新方法：

```python
    async def request_end_conversation(self) -> None:
        """由 end_conversation 工具调用：标记会话即将结束。
        不立即关闭——等当前 TTS（告别语）播完后由 _on_tts_state 触发。"""
        self._ending = True
```

- [ ] **Step 5: _on_final 加 _ending 守卫**

在 `_on_final` 方法开头，把

```python
    async def _on_final(self, text: str) -> None:
        if not self._running:
            return
        text = text.strip()
```

改为（插一行守卫）：

```python
    async def _on_final(self, text: str) -> None:
        if not self._running:
            return
        if self._ending:
            # 收尾期间忽略新输入，避免打断告别语 / 破坏结束流程
            return
        text = text.strip()
```

- [ ] **Step 6: _run_turn 构造 ctx 时注入回调**

在 `_run_turn` 的 tool 执行处，把

```python
                    ctx = ToolContext(call_id=call_id, args=args, request_photo=self.request_photo)
```

改为：

```python
                    ctx = ToolContext(
                        call_id=call_id,
                        args=args,
                        request_photo=self.request_photo,
                        request_end_conversation=self.request_end_conversation,
                    )
```

- [ ] **Step 7: _run_turn 加结束分支**

在 `_run_turn` 的内层 tool 循环之后（紧跟 `if not active(): tts.cancel(); return`、在「带 tool 结果进入下一轮 astream_once」注释之前），插入结束判断。即把

```python
                    self.llm.add_tool(call_id, result.to_message_content())
                    if not active():
                        tts.cancel()
                        return
                # 带 tool 结果进入下一轮 astream_once
```

改为：

```python
                    self.llm.add_tool(call_id, result.to_message_content())
                    if not active():
                        tts.cancel()
                        return
                # end_conversation 已请求结束：不再调 LLM，让告别语 TTS 自然播完
                if self._ending:
                    if active():
                        tts.finish()
                    break
                # 带 tool 结果进入下一轮 astream_once
```

- [ ] **Step 8: 跑测试确认通过**

Run: `uv run pytest tests/test_session_end.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 9: 回归——跑全部测试**

Run: `uv run pytest -q`
Expected: 全绿（确认未破坏既有 session/photo/mcp 测试）

- [ ] **Step 10: 提交**

```bash
git add app/session.py tests/test_session_end.py
git commit -m "feat(session): end_conversation 触发 _ending 收尾（跳出 LLM + 忽略输入）"
```

---

## Task 5: Session 收尾 —— tts_end 发 conversation_end + 30s 兜底关闭

**Files:**
- Modify: `app/session.py`
- Test: `tests/test_session_end.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_session_end.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_session_end.py::test_tts_end_emits_conversation_end_when_ending -v`
Expected: FAIL（`_on_tts_state` 仍走原逻辑，不发 `conversation_end` / `_force_close_after` 不存在）

- [ ] **Step 3: 改 _on_tts_state 的 tts_end 分支**

在 `app/session.py` 的 `_on_tts_state`，把

```python
        elif event == "tts_end":
            await self._send({"type": "tts_end"})
            await self._set_state("listening")
```

改为：

```python
        elif event == "tts_end":
            await self._send({"type": "tts_end"})
            if self._ending:
                # 告别语已播完：通知前端收尾 + 兜底强制关闭
                await self._send({"type": "conversation_end"})
                self._end_fallback = asyncio.create_task(self._force_close_after(30.0))
            else:
                await self._set_state("listening")
```

- [ ] **Step 4: 新增 _force_close_after 方法**

在 `request_end_conversation` 方法之后插入：

```python
    async def _force_close_after(self, delay: float) -> None:
        """兜底：delay 秒后若会话仍存活，主动关闭 WS（防前端未关）。"""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._running:
            log.info("结束对话兜底：主动关闭 WS（%ss 内前端未关）", delay)
            try:
                await self.ws.close()
            except Exception:
                log.debug("兜底关闭 WS 失败", exc_info=True)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_session_end.py -v`
Expected: PASS（全部 8 个测试通过）

- [ ] **Step 6: 回归——跑全部测试**

Run: `uv run pytest -q`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add app/session.py tests/test_session_end.py
git commit -m "feat(session): tts_end 发 conversation_end + 30s 兜底关闭 WS"
```

---

## Task 6: 前端 —— conversation_end 事件处理（播完再断连）

**Files:**
- Modify: `static/app.js`

> 前端无 JS 单测框架，本任务靠代码审查 + 浏览器手测验证。

- [ ] **Step 1: 加 endingByVoice 标志**

在 `static/app.js` 的状态变量区（`let sources = [];` 那一行之后）追加：

```js
let endingByVoice = false;
```

- [ ] **Step 2: playPcm 的 onended 加「播完即关」**

在 `playPcm` 中把

```js
  node.onended = () => { sources = sources.filter((s) => s !== node); };
```

改为：

```js
  node.onended = () => {
    sources = sources.filter((s) => s !== node);
    // 语义结束：告别语播完（队列空）再断连，避免截断尾音
    if (endingByVoice && sources.length === 0) stopSession();
  };
```

- [ ] **Step 3: handleEvent 加 conversation_end 分支**

在 `handleEvent` 的 switch 中（`case "cancel_playback":` 分支之后）插入：

```js
    case "conversation_end":
      flushTypewriter();
      endingByVoice = true;
      if (sources.length === 0) {
        // 已无音频在播，短延迟后收尾（让最后一块 PCM 落地）
        setTimeout(() => { if (endingByVoice) stopSession(); }, 300);
      }
      break;
```

- [ ] **Step 4: 手动验证**

启动后端：`uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

浏览器打开 http://127.0.0.1:8000，点「开始对话」，授权麦克风+摄像头，开口说「拜拜」：

- 预期：助手回一句告别语（如「好的，再见！」），**播报完整不截断**，随后连接断开、前端回到「开始对话」初始态（与点「结束对话」按钮效果一致）。

再说一句非结束的话（如「讲个笑话」），确认正常对话不受影响。

- [ ] **Step 5: 提交**

```bash
git add static/app.js
git commit -m "feat(web): conversation_end 前端处理（等播放队列空再断连）"
```

---

## Task 7: README 文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 配置项表加 ENABLE_END_BY_VOICE**

在 `README.md` 的配置项表（`MAX_TOOL_CALLS_PER_TURN` 行之后、`ENABLE_MCP` 行之前）插入一行：

```markdown
| `ENABLE_END_BY_VOICE` | `true` | 是否启用「语义结束」（说再见等由 LLM 调 `end_conversation` 结束） |
```

- [ ] **Step 2: 协议说明加 conversation_end 事件**

在 `README.md` 的「服务端 → 客户端」事件列表中，把视觉相关那行之后追加 `conversation_end`。即找到：

```markdown
  - `tts_format` / `state` / `partial`（实时转写）/ `user_final` / `delta`（助手增量）/ `tts_start` / `tts_end` / `cancel_playback` / `error`；视觉相关：`take_photo`（要求拍照，含 `call_id`）/ `tool_running`（工具执行中，含 `tool` 名）/ `vision_config`（下发拍照参数）。
```

在其末尾的「下发拍照参数）。」之后追加一句：

```markdown
  另有 `conversation_end`（语义结束：助手告别语播完后下发，前端据此在播放队列空时断连回初始态）。
```

- [ ] **Step 3: 项目结构加 end_conversation.py**

在 `README.md` 项目结构的 `tools/` 区块，把

```
│   │   └── builtin/take_photo.py
```

改为：

```
│   │   ├── builtin/take_photo.py
│   │   └── builtin/end_conversation.py
```

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: end_conversation 配置项、协议事件与项目结构"
```

---

## 完成标准（Definition of Done）

- [ ] `uv run pytest -q` 全绿
- [ ] 浏览器手测：说「拜拜」→ 告别语播完 → 自动断连回初始态；说「再见亦是朋友」不触发；speaking 时说「不聊了再见」barge-in 后正常结束；`ENABLE_END_BY_VOICE=false` 时说「再见」不触发
- [ ] README 配置项 / 协议事件 / 项目结构已更新
- [ ] 每个 Task 独立提交
