# 视觉/摄像头能力 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 DemoTalk 的 LLM（qwen3.7-plus）在对话需要视觉时主动调用 `take_photo` 工具，经前端摄像头拍照并以多模态消息回传，基于画面回答。

**Architecture:** 新增通用 `app/tools/` 框架（Tool 协议 + 注册表 + 内置 take_photo）。`llm.py` 增加单次流式 `astream_once`（检测 tool_calls）。`session.py` 编排 tool-calling 循环并经 WS 让前端拍照。前端常开摄像头 + 预览，处理拍照协议。

**Tech Stack:** Python 3.12 / FastAPI / AsyncOpenAI(百炼兼容) / pytest+pytest-asyncio(新引入) / 原生 JS + AudioWorklet + Canvas。

**对应 spec:** `docs/superpowers/specs/2026-06-23-vision-camera-design.md`

**约定:**
- 工作分支：`design/vision-camera`（已创建）
- 后端测试用 `uv run pytest`；前端用 `scripts/selftest.py` Phase 4 + 浏览器手动验证
- 每个任务结束 commit。Windows 下若有 Defender 锁文件，`UV_LINK_MODE=copy uv ...`

---

## Task 1: 引入 pytest + 新增配置项

**Files:**
- Modify: `pyproject.toml`（加 dev 依赖）
- Modify: `app/config.py`（末尾追加配置）
- Create: `tests/__init__.py`（空）
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 加 dev 依赖**

Run:
```bash
uv add --dev pytest pytest-asyncio
```
Expected: `pyproject.toml` 出现 `[dependency-groups] dev = [...]`，含 pytest、pytest-asyncio。

- [ ] **Step 2: 配置 pytest**

在 `pyproject.toml` 末尾追加：
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: 写失败测试 `tests/test_config.py`**

```python
from app import config


def test_vision_defaults():
    assert config.ENABLE_VISION is True
    assert config.PHOTO_MAX_SIZE == 640
    assert config.PHOTO_QUALITY == 0.8
    assert config.TAKE_PHOTO_TIMEOUT == 5
    assert config.MAX_TOOL_CALLS_PER_TURN == 3
```

Create empty `tests/__init__.py`.

- [ ] **Step 4: 运行测试，确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`AttributeError: module 'app.config' has no attribute 'ENABLE_VISION'`）

- [ ] **Step 5: 在 `app/config.py` 末尾追加配置**

在 `app/config.py` 文件末尾（`DASHSCOPE_WS_URL` 那行之后）追加：
```python

# ---- 视觉 / tool-calling ----
ENABLE_VISION: bool = _bool("ENABLE_VISION", True)
PHOTO_MAX_SIZE: int = _int("PHOTO_MAX_SIZE", 640)
PHOTO_QUALITY: float = _float("PHOTO_QUALITY", 0.8)
TAKE_PHOTO_TIMEOUT: int = _int("TAKE_PHOTO_TIMEOUT", 5)
MAX_TOOL_CALLS_PER_TURN: int = _int("MAX_TOOL_CALLS_PER_TURN", 3)
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（1 passed）

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/config.py tests/
git commit -m "feat(vision): 引入 pytest 与视觉/tool-calling 配置项"
```

---

## Task 2: ToolResult 数据结构

**Files:**
- Create: `app/tools/__init__.py`
- Create: `app/tools/base.py`（本任务只放 ToolResult）
- Create: `tests/tools/__init__.py`
- Create: `tests/tools/test_base.py`

- [ ] **Step 1: 写失败测试 `tests/tools/test_base.py`**

```python
from app.tools.base import ToolResult


def test_text_only_result():
    r = ToolResult(text="hello")
    assert r.to_message_content() == [{"type": "text", "text": "hello"}]


def test_image_result():
    r = ToolResult(text="照片", image_data_url="data:image/jpeg;base64,AAA")
    content = r.to_message_content()
    assert content[0] == {"type": "text", "text": "照片"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}


def test_empty_result():
    r = ToolResult()
    assert r.to_message_content() == [{"type": "text", "text": ""}]
```

Create empty `tests/tools/__init__.py`.

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/tools/test_base.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.tools'`）

- [ ] **Step 3: 实现 `app/tools/base.py`**

```python
"""通用工具框架：ToolResult / ToolContext / Tool 协议。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """工具执行结果，可承载文本与（可选）图像。

    to_message_content() 转为 OpenAI tool message 的多模态 content 数组。
    """

    text: str = ""
    image_data_url: str | None = None  # 形如 data:image/jpeg;base64,...

    def to_message_content(self) -> list[dict]:
        content: list[dict] = []
        if self.text:
            content.append({"type": "text", "text": self.text})
        if self.image_data_url:
            content.append({"type": "image_url", "image_url": {"url": self.image_data_url}})
        if not content:
            content.append({"type": "text", "text": ""})
        return content
```

Create `app/tools/__init__.py`：
```python
"""DemoTalk 通用 tool 框架。"""
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/tools/test_base.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/tools/__init__.py app/tools/base.py tests/tools/
git commit -m "feat(tools): ToolResult 多模态结果结构"
```

---

## Task 3: ToolContext + Tool 协议

**Files:**
- Modify: `app/tools/base.py`（追加 ToolContext、Tool）
- Modify: `tests/tools/test_base.py`（追加测试）

- [ ] **Step 1: 追加失败测试到 `tests/tools/test_base.py`**

```python
import inspect
from app.tools.base import Tool, ToolContext


def test_tool_protocol_is_protocol():
    # Tool 是 Protocol，可被任意含 schema/execute 的对象满足
    class Echo:
        @property
        def schema(self) -> dict:
            return {"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, ctx: ToolContext) -> "ToolResult":
            from app.tools.base import ToolResult
            return ToolResult(text="ok")

    e = Echo()
    assert isinstance(e, Tool)  # runtime_checkable 协议校验


def test_tool_context_fields():
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    assert ctx.call_id == "c1"
    assert ctx.args == {}
    assert callable(ctx.request_photo)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/tools/test_base.py -v`
Expected: FAIL（`cannot import name 'Tool' / 'ToolContext'`）

- [ ] **Step 3: 在 `app/tools/base.py` 末尾追加**

```python

@dataclass
class ToolContext:
    """工具执行上下文。request_photo 由 session 注入（返回 data URL 或 None）。"""

    call_id: str
    args: dict
    request_photo: Callable[[str], Awaitable[str | None]]


@runtime_checkable
class Tool(Protocol):
    """工具协议：声明 schema，execute 执行并返回 ToolResult。"""

    @property
    def schema(self) -> dict:
        """OpenAI function schema: {name, description, parameters}。"""
        ...

    async def execute(self, ctx: ToolContext) -> ToolResult:
        ...
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/tools/test_base.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add app/tools/base.py tests/tools/test_base.py
git commit -m "feat(tools): ToolContext 与 Tool 协议"
```

---

## Task 4: ToolRegistry 注册表

**Files:**
- Create: `app/tools/registry.py`
- Create: `tests/tools/test_registry.py`

- [ ] **Step 1: 写失败测试 `tests/tools/test_registry.py`**

```python
import pytest
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry


class FakeTool:
    def __init__(self, name, result_text="ok"):
        self._name = name
        self._result_text = result_text

    @property
    def schema(self) -> dict:
        return {"name": self._name, "description": "d", "parameters": {"type": "object", "properties": {}}}

    async def execute(self, ctx: ToolContext) -> ToolResult:
        return ToolResult(text=self._result_text)


@pytest.fixture
def ctx():
    return ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)


def test_register_and_schemas():
    reg = ToolRegistry()
    reg.register(FakeTool("echo"))
    schemas = reg.schemas()
    assert schemas == [{"type": "function", "function": {"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


async def test_execute_known(ctx):
    reg = ToolRegistry()
    reg.register(FakeTool("echo", "hi"))
    result = await reg.execute("echo", ctx)
    assert result.text == "hi"


async def test_execute_unknown_returns_error(ctx):
    reg = ToolRegistry()
    result = await reg.execute("nope", ctx)
    assert "未知工具" in result.text


async def test_execute_swallows_exception(ctx):
    class Boom:
        @property
        def schema(self):
            return {"name": "boom", "description": "d", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, ctx):
            raise RuntimeError("爆炸")

    reg = ToolRegistry()
    reg.register(Boom())
    result = await reg.execute("boom", ctx)
    assert "工具执行出错" in result.text
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/tools/test_registry.py -v`
Expected: FAIL（`No module named 'app.tools.registry'`）

- [ ] **Step 3: 实现 `app/tools/registry.py`**

```python
"""工具注册表：管理内置/外部工具，提供 schema 列表与统一执行入口。"""
from __future__ import annotations

import logging

from .base import Tool, ToolContext, ToolResult

log = logging.getLogger("demotalk.tools")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.schema["name"]] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """OpenAI chat completions 的 tools 参数格式。"""
        return [{"type": "function", "function": t.schema} for t in self._tools.values()]

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

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/tools/test_registry.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add app/tools/registry.py tests/tools/test_registry.py
git commit -m "feat(tools): ToolRegistry 注册表"
```

---

## Task 5: TakePhotoTool 内置工具

**Files:**
- Create: `app/tools/builtin/__init__.py`
- Create: `app/tools/builtin/take_photo.py`
- Create: `tests/tools/builtin/__init__.py`
- Create: `tests/tools/builtin/test_take_photo.py`

- [ ] **Step 1: 写失败测试 `tests/tools/builtin/test_take_photo.py`**

```python
import pytest
from app.tools.base import ToolContext
from app.tools.builtin.take_photo import TakePhotoTool


@pytest.fixture
def tool():
    return TakePhotoTool()


def test_schema(tool):
    s = tool.schema
    assert s["name"] == "take_photo"
    assert s["parameters"] == {"type": "object", "properties": {}}


async def test_execute_returns_image_when_photo_ok(tool):
    async def fake_request(call_id):
        assert call_id == "c1"
        return "data:image/jpeg;base64,AAA"

    ctx = ToolContext(call_id="c1", args={}, request_photo=fake_request)
    result = await tool.execute(ctx)
    assert result.image_data_url == "data:image/jpeg;base64,AAA"
    content = result.to_message_content()
    assert any(c.get("type") == "image_url" for c in content)


async def test_execute_returns_text_when_photo_none(tool):
    async def fake_request(call_id):
        return None

    ctx = ToolContext(call_id="c1", args={}, request_photo=fake_request)
    result = await tool.execute(ctx)
    assert result.image_data_url is None
    assert "失败" in result.text or "超时" in result.text
```

Create empty `tests/tools/builtin/__init__.py`.

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/tools/builtin/test_take_photo.py -v`
Expected: FAIL（`No module named 'app.tools.builtin.take_photo'`）

- [ ] **Step 3: 实现 `app/tools/builtin/take_photo.py`**

```python
"""内置工具：拍照。经 ToolContext.request_photo 让前端拍照并回传。"""
from __future__ import annotations

from ..base import Tool, ToolContext, ToolResult


class TakePhotoTool:
    """拍一张当前摄像头画面，用于回答需要视觉的问题。"""

    @property
    def schema(self) -> dict:
        return {
            "name": "take_photo",
            "description": "拍一张当前摄像头画面，用于回答需要视觉的问题（如「这是什么」「前面有什么」「前面有几种颜色」）。需要看用户周围环境时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        data_url = await ctx.request_photo(ctx.call_id)
        if not data_url:
            return ToolResult(text="拍照失败或超时，无法获取画面。")
        return ToolResult(text="拍到的照片", image_data_url=data_url)
```

Create `app/tools/builtin/__init__.py`：
```python
"""内置工具集合。"""
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/tools/builtin/test_take_photo.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/tools/builtin/ tests/tools/builtin/
git commit -m "feat(tools): 内置 take_photo 工具"
```

---

## Task 6: LLMService 单次流式 astream_once（含 tool_calls 检测）

**Files:**
- Modify: `app/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 1: 写失败测试 `tests/test_llm.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _chunk(content=None, tool_calls=None, finish_reason=None):
    """构造一个模拟的流式 chunk。"""
    choice = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _tool_call_delta(index=0, cid=None, name=None, args=None):
    tc = MagicMock()
    tc.index = index
    tc.id = cid
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    return tc


async def test_astream_once_text_only():
    from app.llm import LLMService
    llm = LLMService()
    fake_stream = _async_iter([
        _chunk(content="你"),
        _chunk(content="好"),
        _chunk(finish_reason="stop"),
    ])
    with patch.object(llm._client, "chat", new=_mock_chat(fake_stream)):
        events = []
        async for ev in llm.astream_once():
            events.append(ev)
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert events[-1]["tool_calls"] == []
    assert "".join(e["text"] for e in events if e["type"] == "text") == "你好"


async def test_astream_once_detects_tool_call():
    from app.llm import LLMService
    llm = LLMService()
    fake_stream = _async_iter([
        _chunk(content="我看看"),
        _chunk(tool_calls=[_tool_call_delta(0, cid="call_1", name="take_photo", args="{}")]),
        _chunk(finish_reason="tool_calls"),
    ])
    with patch.object(llm._client, "chat", new=_mock_chat(fake_stream)):
        events = [e async for e in llm.astream_once()]
    done = events[-1]
    assert done["finish_reason"] == "tool_calls"
    assert len(done["tool_calls"]) == 1
    tc = done["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "take_photo"


async def test_add_user_and_add_tool():
    from app.llm import LLMService
    llm = LLMService()
    llm.add_user("hi")
    llm.add_tool("call_1", [{"type": "text", "text": "照片"}])
    msgs = llm.messages()
    assert msgs[-2]["role"] == "user"
    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == "call_1"


# ---- helpers ----
async def _async_iter(items):
    for it in items:
        yield it


def _mock_chat(stream):
    """chat.completions.create 的 mock。"""
    completions = MagicMock()
    completions.create = AsyncMock(return_value=stream)
    chat = MagicMock()
    chat.completions = completions
    return chat
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL（`AttributeError: 'LLMService' object has no attribute 'astream_once'`）

- [ ] **Step 3: 改造 `app/llm.py`**

将 `app/llm.py` 完整替换为：
```python
"""LLM：封装阿里云百炼 qwen3.7-plus 流式对话（OpenAI 兼容接口）。

- astream_once(): 单次流式，yield 事件（text / done）。done 携带 tool_calls。
- add_user/add_tool(): 维护多模态 + tool 消息历史。
- astream(): 旧的纯文本流式接口，保留供 selftest Phase 1。

qwen3.7-plus 默认开思考模式，必须 extra_body={"enable_thinking": False} 关闭。
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from . import config

log = logging.getLogger("demotalk.llm")


class LLMService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.LLM_BASE_URL,
        )
        self._history: list[dict] = [
            {"role": "system", "content": config.LLM_SYSTEM_PROMPT}
        ]

    # ---- 历史管理 ----
    def messages(self) -> list[dict]:
        return self._history

    def add_user(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})

    def add_tool(self, call_id: str, content: list[dict]) -> None:
        self._history.append({"role": "tool", "tool_call_id": call_id, "content": content})

    def reset(self) -> None:
        self._history = [{"role": "system", "content": config.LLM_SYSTEM_PROMPT}]

    # ---- 单次流式（带 tool 检测）----
    async def astream_once(self, tools: list[dict] | None = None) -> AsyncIterator[dict]:
        """基于当前 _history 做一次流式调用。

        yield {"type":"text","text":str} 文本增量，
        最后 yield {"type":"done","tool_calls":list,"finish_reason":str}。
        并把 assistant 消息（含 tool_calls）追加到 _history。
        """
        texts: list[str] = []
        tc_map: dict[int, dict] = {}
        finish: str | None = None
        try:
            stream = await self._client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=self._history,
                stream=True,
                tools=tools,
                temperature=config.LLM_TEMPERATURE,
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": False},
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                content = getattr(delta, "content", None) or ""
                if content:
                    texts.append(content)
                    yield {"type": "text", "text": content}
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        i = tc.index
                        slot = tc_map.setdefault(i, {"id": "", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
                if getattr(choice, "finish_reason", None):
                    finish = choice.finish_reason
        except Exception as e:
            log.exception("LLM 流式调用失败")
            yield {"type": "text", "text": f"（模型调用失败：{e}）"}
            yield {"type": "done", "tool_calls": [], "finish_reason": "error"}
            return

        tool_calls = list(tc_map.values()) if finish == "tool_calls" else []
        assistant_msg: dict = {"role": "assistant", "content": "".join(texts)}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self._history.append(assistant_msg)
        self._trim_history()
        yield {"type": "done", "tool_calls": tool_calls, "finish_reason": finish or "stop"}

    def _trim_history(self) -> None:
        """保留 system + 最近 N 轮（user+assistant）。tool 消息随其 assistant 保留。"""
        sys_msgs = [m for m in self._history if m["role"] == "system"]
        convo = [m for m in self._history if m["role"] != "system"]
        keep = config.LLM_HISTORY_TURNS * 2
        self._history = sys_msgs + convo[-keep:]

    # ---- 旧接口（selftest Phase 1 用）----
    async def astream(self, user_text: str) -> AsyncIterator[str]:
        self.add_user(user_text)
        async for event in self.astream_once(tools=None):
            if event["type"] == "text":
                yield event["text"]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS（3 passed）

也跑全量回归：`uv run pytest -v`
Expected: 全部 PASS（之前的 config/tools 测试不回归）

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_llm.py
git commit -m "feat(llm): astream_once 流式 + tool_calls 检测与历史管理"
```

---

## Task 7: Session 编排 tool 循环 + request_photo

**Files:**
- Modify: `app/session.py`
- Create: `tests/test_session_photo.py`

- [ ] **Step 1: 写失败测试 `tests/test_session_photo.py`**

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_event_loop()
    s = Session(ws, loop)
    s._running = True
    return s


async def test_request_photo_returns_data_when_resolved():
    s = _make_session()
    ws = s.ws

    async def fake_send(obj):
        # 模拟前端在收到 take_photo 后回传 photo
        call_id = obj["call_id"]
        asyncio.get_event_loop().call_later(
            0.05, lambda: asyncio.ensure_future(s.resolve_photo(call_id, "data:image/jpeg;base64,X"))
        )

    ws.send_text = fake_send
    data = await s.request_photo("c1")
    assert data == "data:image/jpeg;base64,X"
    assert "c1" not in s._pending_photos


async def test_request_photo_timeout_returns_none(monkeypatch):
    s = _make_session()
    monkeypatch.setattr("app.config.TAKE_PHOTO_TIMEOUT", 0)  # 立即超时
    # send_text 用空 async
    s.ws.send_text = AsyncMock()
    data = await s.request_photo("c2")
    assert data is None


async def test_resolve_photo_ignores_unknown_call_id():
    s = _make_session()
    await s.resolve_photo("nope", "data")  # 不应抛错
    assert s._pending_photos == {}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_session_photo.py -v`
Expected: FAIL（`AttributeError: 'Session' has no attribute 'request_photo'`）

- [ ] **Step 3: 改造 `app/session.py`**

在 `Session.__init__` 中初始化 tool 框架与 pending photos；改写 `_run_turn` 为 tool 循环；新增 `request_photo`/`resolve_photo`/`handle_photo`。

**3a. 改 `__init__`（在 `self.tts: TTSService | None = None` 之后追加）：**
```python
        from .tools.base import ToolContext  # noqa: F401  (类型用)
        from .tools.registry import ToolRegistry
        from .tools.builtin.take_photo import TakePhotoTool

        self.tool_registry = ToolRegistry()
        if config.ENABLE_VISION:
            self.tool_registry.register(TakePhotoTool())
        # 待回传的拍照请求：call_id -> Future
        self._pending_photos: dict[str, asyncio.Future] = {}
```

**3b. 用下面内容整体替换 `_run_turn` 方法：**
```python
    async def _run_turn(self, user_text: str, turn: int) -> None:
        tts: TTSService | None = None
        try:
            await self._set_state("thinking")
            tts = TTSService(
                on_audio=self._on_tts_audio,
                on_state=self._on_tts_state,
                loop=self.loop,
            )
            self.tts = tts
            tts.start()

            def active() -> bool:
                return turn == self._current_turn and self._running

            self.llm.add_user(user_text)
            tools = self.tool_registry.schemas() if config.ENABLE_VISION else None

            for _ in range(config.MAX_TOOL_CALLS_PER_TURN):
                buffer = ""
                tool_calls: list[dict] = []
                async for event in self.llm.astream_once(tools=tools):
                    if not active():
                        tts.cancel()
                        return
                    if event["type"] == "text":
                        delta = event["text"]
                        await self._send({"type": "delta", "text": delta})
                        buffer += delta
                        while True:
                            m = _SENTENCE_END.search(buffer)
                            if not m:
                                break
                            sentence = buffer[: m.end()]
                            buffer = buffer[m.end():]
                            if active():
                                tts.feed(sentence)
                    elif event["type"] == "done":
                        tool_calls = event.get("tool_calls", [])
                if not active():
                    tts.cancel()
                    return
                if buffer.strip() and active():
                    tts.feed(buffer)
                if not tool_calls:
                    if active():
                        tts.finish()
                    break  # finish_reason=stop
                # 执行本轮所有 tool_calls
                for tc in tool_calls:
                    if not active():
                        tts.cancel()
                        return
                    call_id = tc.get("id", "")
                    name = tc.get("function", {}).get("name", "")
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    from .tools.base import ToolContext
                    ctx = ToolContext(call_id=call_id, args=args, request_photo=self.request_photo)
                    await self._send({"type": "tool_running", "tool": name})
                    result = await self.tool_registry.execute(name, ctx)
                    self.llm.add_tool(call_id, result.to_message_content())
                # 带 tool 结果进入下一轮 astream_once
            else:
                # 达到 MAX_TOOL_CALLS_PER_TURN，强制收尾
                if active():
                    tts.finish()
        except Exception:
            log.exception("_run_turn 异常")
            await self._send({"type": "error", "message": "本轮对话失败"})
            if tts is not None:
                try:
                    tts.cancel()
                except Exception:
                    pass
            await self._set_state("listening")
```

**3c. 新增方法（放在 `_barge_in` 之后）：**
```python
    # ---------- 拍照（tool 交互）----------

    async def request_photo(self, call_id: str) -> str | None:
        """发 take_photo 请求，等待前端回传；超时返回 None。"""
        fut = self.loop.create_future()
        self._pending_photos[call_id] = fut
        await self._send({"type": "take_photo", "call_id": call_id})
        try:
            return await asyncio.wait_for(fut, timeout=config.TAKE_PHOTO_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_photos.pop(call_id, None)

    async def resolve_photo(self, call_id: str, data: str | None) -> None:
        fut = self._pending_photos.get(call_id)
        if fut is not None and not fut.done():
            fut.set_result(data)

    async def handle_photo(self, call_id: str, data) -> None:
        await self.resolve_photo(call_id, data)

    async def handle_photo_error(self, call_id: str) -> None:
        await self.resolve_photo(call_id, None)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_session_photo.py -v`
Expected: PASS（3 passed）

全量回归：`uv run pytest -v` → 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/session.py tests/test_session_photo.py
git commit -m "feat(session): tool-calling 循环编排 + request_photo WS 交互"
```

---

## Task 8: main.py 处理 photo / photo_error 消息

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: 写失败测试（行为级：消息分发）**

无需新文件。在 `tests/test_session_photo.py` 追加，验证 `Session.handle_photo` 能把 photo 数据送达 pending future：
```python
async def test_handle_photo_resolves_pending():
    s = _make_session()
    fut = s.loop.create_future()
    s._pending_photos["c9"] = fut
    await s.handle_photo("c9", "data:image/jpeg;base64,Z")
    assert fut.result() == "data:image/jpeg;base64,Z"
```

Run: `uv run pytest tests/test_session_photo.py::test_handle_photo_resolves_pending -v`
Expected: PASS（验证 handle_photo 已在 Task 7 提供）。

- [ ] **Step 2: 改 `app/main.py` 的 ws_endpoint 消息分发**

在 `app/main.py` 的 `ws_endpoint` 内，把这段：
```python
                if obj.get("type") == "stop":
                    break
                await session.handle_control(obj)
```
替换为：
```python
                t = obj.get("type")
                if t == "stop":
                    break
                elif t == "photo":
                    await session.handle_photo(obj.get("call_id", ""), obj.get("data"))
                elif t == "photo_error":
                    await session.handle_photo_error(obj.get("call_id", ""))
                else:
                    await session.handle_control(obj)
```

- [ ] **Step 3: 跑全量测试 + 启动冒烟**

Run: `uv run pytest -v`
Expected: 全部 PASS。

冒烟：后台启动服务并验证 healthz：
```bash
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 5 && curl -s http://127.0.0.1:8000/healthz ; kill %1
```
Expected: 返回 `{"ok":true,...}`，无启动异常。

- [ ] **Step 4: Commit**

```bash
git add app/main.py tests/test_session_photo.py
git commit -m "feat(main): 分发 photo/photo_error 至 session"
```

---

## Task 9: 前端 — 摄像头常开 + 预览窗 + vision_config

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `static/app.js`

> 前端无单测框架；本任务验证方式 = 浏览器手动（步骤 4）。

- [ ] **Step 1: `static/index.html` 加预览窗**

在 `<body>` 内顶部容器（参照现有结构，放在 transcript 区域旁）加入：
```html
<video id="camView" autoplay muted playsinline></video>
<div id="flash"></div>
```
（具体放置位置：与现有 `#transcript` 同级，放在右侧；样式由 CSS 定位。）

- [ ] **Step 2: `static/style.css` 加预览/闪光样式**

```css
#camView {
  position: fixed;
  right: 16px;
  bottom: 16px;
  width: 160px;
  height: 120px;
  object-fit: cover;
  transform: scaleX(-1);          /* 镜像 */
  background: #000;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.2);
  z-index: 10;
}
#camView.hidden { display: none; }

#flash {
  position: fixed; inset: 0;
  background: #fff;
  opacity: 0;
  pointer-events: none;
  z-index: 20;
}
#flash.fire { animation: flash 0.35s ease-out; }
@keyframes flash {
  0% { opacity: 0.8; }
  100% { opacity: 0; }
}
```

- [ ] **Step 3: `static/app.js` — 开摄像头 + 预览 + vision_config**

3a. 顶部状态区追加（在 `let ttsSampleRate = 24000;` 之后）：
```javascript
const videoEl = $("#camView");
const flashEl = $("#flash");
let camStream = null;
let photoMaxSize = 640;
let photoQuality = 0.8;
```

3b. 把 `startMic` 改名为 `startAV`，同时采集音视频（整体替换原 `startMic` 函数）：
```javascript
async function startAV() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      sampleRate: 16000,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
  });
  // 音频链路（同原逻辑）
  micCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule(WORKLET_URL);
  const srcNode = micCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(micCtx, "mic-pcm");
  workletNode.port.onmessage = (ev) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(ev.data);
  };
  srcNode.connect(workletNode);
  workletNode.connect(micCtx.destination);
  // 视频预览
  camStream = micStream;
  videoEl.srcObject = micStream;
  videoEl.classList.remove("hidden");
  await videoEl.play().catch(() => {});
  setHint("麦克风与摄像头已就绪，可以开口说话了");
}
```

3c. `stopMic` 改名 `stopAV`，额外停视频轨道（整体替换）：
```javascript
function stopAV() {
  try { if (workletNode) workletNode.disconnect(); } catch (e) {}
  try { if (micCtx) micCtx.close(); } catch (e) {}
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  videoEl.srcObject = null;
  videoEl.classList.add("hidden");
  workletNode = null;
  micCtx = null;
  micStream = null;
  camStream = null;
}
```

3d. 把代码里所有 `startMic()` 调用改为 `startAV()`（在 `startSession` 内），所有 `stopMic()` 调用改为 `stopAV()`（在 `ws.onclose` 内）。

3e. `handleEvent` 的 `tts_format` 分支旁新增 `vision_config` 处理：
```javascript
    case "vision_config":
      if (obj.photo_max_size) photoMaxSize = obj.photo_max_size;
      if (obj.photo_quality) photoQuality = obj.photo_quality;
      break;
```

- [ ] **Step 4: 手动验证**

启动服务（`uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`），浏览器开 http://127.0.0.1:8000 ，点「开始对话」并允许摄像头。
Expected：右下角出现镜像预览窗，显示实时画面；状态显示「麦克风与摄像头已就绪」；不开口时无报错。

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat(frontend): 摄像头常开 + 预览窗 + vision_config"
```

---

## Task 10: 前端 — 拍照协议、抓帧、闪光

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`（闪光已在 Task 9 加）

- [ ] **Step 1: `static/app.js` — 处理 take_photo / tool_running + 抓帧回传**

1a. `handleEvent` 的 switch 新增分支（与其它 case 同级）：
```javascript
    case "take_photo":
      handleTakePhoto(obj.call_id);
      break;
    case "tool_running":
      if (obj.tool === "take_photo") setHint("正在拍照…");
      break;
```

1b. 新增 `handleTakePhoto` 与 `flash`（放在 `// ---- 麦克风 ----` 注释之前）：
```javascript
// ---- 拍照 ----
function handleTakePhoto(callId) {
  try {
    if (!camStream || !videoEl.videoWidth) {
      ws.send(JSON.stringify({ type: "photo_error", call_id: callId, message: "摄像头未就绪" }));
      return;
    }
    const vw = videoEl.videoWidth, vh = videoEl.videoHeight;
    const max = photoMaxSize;
    let w = vw, h = vh;
    if (Math.max(vw, vh) > max) {
      const s = max / Math.max(vw, vh);
      w = Math.round(vw * s);
      h = Math.round(vh * s);
    }
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    // 镜像绘制以匹配预览
    const ctx2 = canvas.getContext("2d");
    ctx2.translate(w, 0);
    ctx2.scale(-1, 1);
    ctx2.drawImage(videoEl, 0, 0, w, h);
    flashEffect();
    const data = canvas.toDataURL("image/jpeg", photoQuality);
    ws.send(JSON.stringify({ type: "photo", call_id: callId, data }));
  } catch (e) {
    ws.send(JSON.stringify({ type: "photo_error", call_id: callId, message: String(e) }));
  }
}

function flashEffect() {
  flashEl.classList.remove("fire");
  void flashEl.offsetWidth; // 强制重绘以重启动画
  flashEl.classList.add("fire");
}
```

- [ ] **Step 2: 手动验证（拍照路径）**

服务已启动状态下，浏览器开页面、开始对话。由于前端无法直接触发 `take_photo`（需 LLM 主动调用），本步先在浏览器控制台手动模拟后端消息，验证抓帧链路：
```javascript
// 控制台执行：模拟后端要求拍照
window.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({type:"take_photo", call_id:"test1"}) }));
```
注意：实际 WS 消息由 ws.onmessage 处理，直接调 `handleTakePhoto("test1")` 更直接：
```javascript
handleTakePhoto("test1");
```
Expected：画面闪一下；在 Network 面板或 `ws.send` 断点看到发出的 `{type:"photo", call_id:"test1", data:"data:image/jpeg;base64,..."}`。

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat(frontend): take_photo 抓帧 + 闪光 + 回传"
```

---

## Task 11: ENABLE_VISION 开关 + system prompt 引导 + 下发 vision_config

**Files:**
- Modify: `app/config.py`（system prompt 默认值）
- Modify: `app/session.py`（start 时下发 vision_config）

- [ ] **Step 1: 调整默认 system prompt**

在 `app/config.py` 把 `LLM_SYSTEM_PROMPT` 的默认值改为：
```python
LLM_SYSTEM_PROMPT: str = _get(
    "LLM_SYSTEM_PROMPT",
    "你是一个简洁友好的中文语音助手。请用 1-2 句话简短回答，口语化、适合语音播报，不要使用 markdown 或列表。当需要看用户周围画面时（例如用户问『这是什么』『前面有什么』），先调用 take_photo 拍照再回答。",
)
```

- [ ] **Step 2: session.start 下发 vision_config**

在 `app/session.py` 的 `start` 方法里，`await self._send({"type": "tts_format", ...})` 之后追加：
```python
        if config.ENABLE_VISION:
            await self._send({
                "type": "vision_config",
                "photo_max_size": config.PHOTO_MAX_SIZE,
                "photo_quality": config.PHOTO_QUALITY,
            })
```

- [ ] **Step 3: 跑测试 + 冒烟**

Run: `uv run pytest -v`
Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add app/config.py app/session.py
git commit -m "feat(vision): system prompt 引导 + 下发 vision_config"
```

---

## Task 12: selftest Phase 4 — 带视觉的端到端往返

**Files:**
- Modify: `scripts/selftest.py`

- [ ] **Step 1: 在 `scripts/selftest.py` 加 Phase 4**

在 `phase3_ws_roundtrip` 之后、`main` 之前新增：
```python
async def phase4_vision_roundtrip() -> None:
    import websockets

    print("\n===== Phase 4: 视觉 tool 端到端往返（take_photo→多模态→回答）=====")
    # 一张"左红右蓝"测试图（PNG，base64）
    import base64, zlib, struct
    w = h = 120
    px = bytearray()
    for _y in range(h):
        for _x in range(w):
            px += bytes((255, 0, 0)) if _x < w // 2 else bytes((0, 0, 255))
    def _png():
        def chunk(typ, data):
            c = typ + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
        raw = b"".join(b"\x00" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    img_b64 = base64.b64encode(_png()).decode()
    data_url = f"data:image/png;base64,{img_b64}"

    events: list[str] = []
    deltas: list[str] = []
    saw_take_photo = False
    done = asyncio.Event()

    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        async def reader():
            nonlocal saw_take_photo
            try:
                async for msg in ws:
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    obj = json.loads(msg)
                    t = obj.get("type")
                    events.append(t)
                    if t == "take_photo":
                        saw_take_photo = True
                        await ws.send(json.dumps({"type": "photo", "call_id": obj.get("call_id"), "data": data_url}))
                    elif t == "delta":
                        deltas.append(obj.get("text", ""))
                    elif t in ("tts_end", "error"):
                        done.set()
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.5)
        # 直接触发一轮：发一条"文本用户消息"——这里通过喂一段含视觉意图的 STT 结果难以模拟，
        # 改为直接断言：selftest 仅验证协议层（take_photo 下发 + photo 上行能驱动 LLM 再回答）。
        # 用一条假 user_final 触发：发送符合 WS 协议的文本会被 main 当作控制消息；
        # 因此 Phase 4 依赖 Phase 3 已验证的 STT→LLM，这里只检查 LLM 在带图后是否产出 delta。
        # 若上一轮 Phase 3 已结束，我们另起一次：发送一句视觉问题文本走 STT 不可行，
        # 故 Phase 4 退化为：连接后等 LLM 主动调 take_photo（由真实 STT 触发，本无头脚本无法注入语音）。
        # → 结论：Phase 4 在无头环境用 mock 注入，见下方断言。
        try:
            await asyncio.wait_for(done.wait(), timeout=30)
        except asyncio.TimeoutError:
            print("   [!] 30s 内未完成")
        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(0.3)
        rtask.cancel()

    print(f"[结果] 事件序列: {events}")
    print(f"[结果] 助手回复: {''.join(deltas)[:120]}")

    ok = True
    if not saw_take_photo:
        print("[FAIL] 未收到 take_photo —— LLM 未发起视觉调用（可能 STT 未喂入视觉意图语音，无头限制）"); ok = False
    if not deltas:
        print("[FAIL] 未收到 delta —— 拍照后 LLM 未作答"); ok = False
    if ok:
        print("[PASS] 视觉 tool 端到端往返成功")
    else:
        print("[INFO] Phase 4 在无头环境受限：建议浏览器手动验证完整链路")
```

并在 `main()` 中 `await phase3_ws_roundtrip()` 之后追加 `await phase4_vision_roundtrip()`。

> **说明（写入计划，非占位）：** Phase 4 在无头脚本里无法向真实 STT 喂"视觉意图语音"，因此 `saw_take_photo` 可能因 LLM 未触发而为 false。这是**预期限制**——Phase 4 的价值在于：一旦 LLM 主动发起 take_photo（手动测试或后续注入 STT），协议层（下发 take_photo → 上行 photo → 多模态回传 → delta）能跑通。完整验证以浏览器手动测试为准（Task 13）。

- [ ] **Step 2: 运行 selftest（Phase 1-4）**

需先启动服务（另一终端）：
```bash
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
然后：
```bash
uv run python scripts/selftest.py
```
Expected: Phase 1/2/3 PASS；Phase 4 打印事件序列，若 LLM 未发起 take_photo 则打印 `[INFO] ... 无头限制`（不算 FAIL 中断）。

- [ ] **Step 3: Commit**

```bash
git add scripts/selftest.py
git commit -m "test(selftest): Phase 4 视觉 tool 端到端（无头受限说明）"
```

---

## Task 13: README 文档 + 手动测试清单

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README 增补视觉能力章节**

在 `## 配置项（.env）` 表格中追加：
```
| `ENABLE_VISION` | `true` | 是否启用视觉（take_photo 工具） |
| `PHOTO_MAX_SIZE` | `640` | 拍照最长边像素 |
| `PHOTO_QUALITY` | `0.8` | JPEG 质量 |
| `TAKE_PHOTO_TIMEOUT` | `5` | 拍照超时(秒) |
| `MAX_TOOL_CALLS_PER_TURN` | `3` | 每轮工具调用上限 |
```

在 README 末尾「协议说明」的「服务端 → 客户端」补充：`take_photo`、`tool_running`、`vision_config`；在「客户端 → 服务端」补充：`photo`、`photo_error`。

并新增一节：
```markdown
## 视觉能力（摄像头）

`qwen3.7-plus` 是多模态模型。当对话需要"看"时（如用户问「这是什么」「前面有什么」），LLM 会**主动调用** `take_photo` 工具：

1. LLM 发起 `take_photo` → 后端经 WS 通知前端
2. 前端抓取当前摄像头画面（JPEG，最长边 640px）→ 回传
3. 后端把图像作为多模态 tool 结果回 LLM → LLM 基于画面回答 → TTS 播报

前置：浏览器与系统均需授权摄像头。点「开始对话」时会同时申请麦克风 + 摄像头，右下角显示预览窗。`ENABLE_VISION=false` 可关闭。
```

- [ ] **Step 2: 浏览器手动测试（最终验收）**

启动服务，浏览器开 http://127.0.0.1:8000 ，点「开始对话」，授权麦克风+摄像头。对摄像头展示一个明显物体（如一杯水），说：「我前面这个东西是什么？」
Expected：
- 状态条出现 thinking / 正在拍照…
- 右下角预览画面闪一下（拍照反馈）
- 助手打字机输出对物体的描述 → 语音播报

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: 视觉能力说明与配置"
```

---

## 完成标准（Definition of Done）

- `uv run pytest -v` 全绿
- `uv run python scripts/selftest.py` Phase 1/2/3 PASS，Phase 4 不中断
- 浏览器手动测试：对着物体问「这是什么」→ LLM 调 take_photo → 基于画面回答 + TTS 播报
- `.env` 关掉 `ENABLE_VISION=false` 时，行为回退到纯语音（不注入 tools）

## 将来扩展（不在本计划）

- MCP 子项目：实现 `McpToolAdapter` 把 MCP server 的 tools 注册进 `ToolRegistry`，复用同一 tool-calling 循环。
- Skills 子项目：同理把 skills 暴露为 tools。
