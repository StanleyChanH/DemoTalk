# 语义结束对话（end_conversation 工具）— 设计

- 日期：2026-06-27
- 状态：已与用户确认设计，待 spec 审阅
- 关联：复用 [[2026-06-23-vision-camera-design]] 的 tool-calling 框架（`app/tools/`）

## 背景与目标

现状：DemoTalk 只能通过前端「结束对话」按钮结束会话（发 `{type:"stop"}` + `ws.close()` → 后端 `ws_endpoint` `break` → `session.shutdown()`）。

目标：增加**语义结束**——当用户用自然语言表达结束对话的意图（「再见」/「拜拜」/「结束对话」/「不聊了」/「先这样吧」/「挂了」等），系统自动结束对话，无需手动点按钮，交互更自然。

## 关键决策（已与用户确认）

1. **判定方式**：LLM 主动调用 `end_conversation` 工具（复用现有 tool-calling 框架，与视觉 `take_photo`、MCP 同机制）。语义准确、能区分语境、助手可自然告别。不采用关键词规则匹配（易误判）或混合方案。
2. **结束行为**：等告别语 TTS 播完后关闭 WS 连接，前端回到「开始对话」初始态——与点按钮**完全一致**。不采用「软结束保持连接」。
3. **兜底超时**：**30 秒**（后端发 `conversation_end` 后若前端未关 WS，强制 `ws.close()`）。
4. **告别语**：**不强制**。LLM 自由决定是否说告别语（system prompt 仅「建议」先简短告别，非硬约束）。
5. **触发敏感度**：平衡——仅明确结束意图才触发（system prompt 引导）。

## 非目标（YAGNI）

- 不做关键词规则匹配 / 混合判定。
- 不做「软结束保持连接」的中间态。
- 不强制告别语内容/长度。
- 不做 selftest 自动化（依赖 LLM 真实意图判断，难无头复现，与 MCP Phase 5 同理）。

## 设计方案

### 模块清单

| 文件 | 改动 | 职责 |
|---|---|---|
| `app/tools/base.py` | 改 | `ToolContext` 新增可选 `request_end_conversation` 回调 |
| `app/tools/builtin/end_conversation.py` | 新增 | `EndConversationTool` |
| `app/session.py` | 改 | 注册 tool、`_ending` 标志、收尾时序、`_on_final` 守卫、兜底关闭 |
| `app/config.py` | 改 | 新增 `ENABLE_END_BY_VOICE`；默认 prompt 追加引导 |
| `.env.example` | 改 | 增补 `ENABLE_END_BY_VOICE` |
| `static/app.js` | 改 | `conversation_end` 事件处理 + 播放队列空后 `stopSession` |
| `tests/tools/builtin/test_end_conversation.py` | 新增 | tool 单测 |
| `tests/test_session_end.py` | 新增 | session 收尾单测 |
| `README.md` | 改 | 配置项 + 协议事件说明 |

### 1. ToolContext 扩展（`app/tools/base.py`）

```python
@dataclass
class ToolContext:
    call_id: str
    args: dict
    request_photo: Callable[[str], Awaitable[str | None]]
    request_end_conversation: Callable[[], Awaitable[None]] | None = None
```

`request_end_conversation` 默认 `None`（可选，放在无默认值字段之后，符合 dataclass 规则），保持对 MCP 适配器等其他 tool 的兼容（它们不传该回调）。`Session` 构造 ctx 时传入 `request_end_conversation=self.request_end_conversation`。

### 2. EndConversationTool（`app/tools/builtin/end_conversation.py`）

```python
class EndConversationTool:
    """用户明确要结束对话时，由 LLM 调用，触发会话优雅关闭。"""

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

### 3. Session 改动（`app/session.py`）

- `__init__`：
  - `if config.ENABLE_END_BY_VOICE: self.tool_registry.register(EndConversationTool())`
  - `self._ending = False`
  - `self._end_fallback: asyncio.Task | None = None`
- 新增 `async def request_end_conversation(self)`：`self._ending = True`（仅置标志，不立即关闭，等告别语播完）。
- `_on_final` 开头：`if self._ending: return`——收尾期间忽略新语音，避免打断告别语 / 破坏结束流程。
- `_run_turn` tool 循环：执行每个 tool 后，`if self._ending: tts.finish(); break`——确保告别语合成收尾 + 不再调 LLM。`add_tool` 仍照常执行（保持历史 API 一致，无害）。
- `_on_tts_state` 的 `tts_end` 分支：
  ```python
  elif event == "tts_end":
      await self._send({"type": "tts_end"})
      if self._ending:
          await self._send({"type": "conversation_end"})
          self._end_fallback = asyncio.create_task(self._force_close_after(30.0))
      else:
          await self._set_state("listening")
  ```
- 新增 `async def _force_close_after(self, delay: float)`：`await asyncio.sleep(delay)`；若 `self._running` 仍为 True，则 `await self.ws.close()`（触发 `ws_endpoint` 的 `receive` 退出 → `finally: session.shutdown()`）。
- 构造 `ToolContext` 处补 `request_end_conversation=self.request_end_conversation`。

**barge-in 兼容性**：speaking 时用户说「再见」→ `_on_final` 检测 speaking → `_barge_in`（取消当前 TTS、`_current_turn++`、新 turn）→ 新 turn 正常处理「再见」→ 触发 `end_conversation`。`_ending` 在 `_barge_in` 中无需特殊处理（barge-in 开启新 turn，`_ending` 仍为 False 直到新 turn 内的 `end_conversation` 执行）。

**边界**：若同一轮 LLM 既调 `take_photo` 又调 `end_conversation`（如「看一眼这个，然后再见」），`_run_turn` 顺序执行：先 `take_photo`（已 `add_tool`），再 `end_conversation`（置 `_ending`），随后 `break`。`take_photo` 结果不再回灌 LLM——可接受。

### 4. system prompt（`app/config.py`）

默认 `LLM_SYSTEM_PROMPT` 末尾追加：

> 当用户明确表达结束对话（如『再见／拜拜／结束对话／不聊了／先这样吧／挂了』）时，可先用一句话简短告别，并调用 `end_conversation` 工具结束对话；无明确结束意图时不要调用。

「可」字体现**不强制**告别语。

> 注：`LLM_SYSTEM_PROMPT` 可被 `.env` 覆盖；用户自定义 prompt 时需自行包含此引导，否则 LLM 不知有该工具。

### 5. 前端（`static/app.js`）

- 新增模块级 `let endingByVoice = false;`。
- `handleEvent` 新增分支：
  ```js
  case "conversation_end":
    flushTypewriter();
    endingByVoice = true;
    if (sources.length === 0) {
      // 已无音频在播，短延迟后收尾
      setTimeout(() => { if (endingByVoice) stopSession(); }, 300);
    }
    break;
  ```
- `playPcm` 的 `node.onended` 追加：
  ```js
  node.onended = () => {
    sources = sources.filter((s) => s !== node);
    if (endingByVoice && sources.length === 0) stopSession();
  };
  ```
- `stopSession` 复用现有（发 `{type:"stop"}` + `ws.close()`）→ `onclose` 回初始态（与按钮一致）。

### 6. 配置（`app/config.py` + `.env.example`）

```python
ENABLE_END_BY_VOICE: bool = _bool("ENABLE_END_BY_VOICE", True)
```

## 数据流

```
用户说"再见"
  → STT final
  → _on_final（_ending 为 False，放行）
  → _run_turn：LLM 流式输出"好的，再见！"（喂 TTS）+ done(tool_calls=[end_conversation])
  → 执行 end_conversation → request_end_conversation → _ending=True
  → 检测 _ending → tts.finish(); break（不再调 LLM）
  → TTS 异步播报告别语 → 合成结束 → _on_tts_state("tts_end")
  → _ending 为 True → 发 tts_end + conversation_end + 启动 30s 兜底
  → 前端 conversation_end → endingByVoice=true
  → 前端播放队列空（source.onended）→ stopSession → 发 stop + ws.close
  → 后端 ws_endpoint 收 stop → break → finally shutdown
  → 前端 onclose → 回初始态
兜底：若前端 30s 内未关 → 后端 _force_close_after 主动 ws.close()
```

## 测试

### 单测

`tests/tools/builtin/test_end_conversation.py`：
1. `EndConversationTool.schema` 含 name/description/parameters。
2. `execute` 调用注入的 `request_end_conversation` 回调（mock 验证被 await）；返回 ToolResult。

`tests/test_session_end.py`：
3. `_run_turn`：LLM 返回 `tool_calls=[end_conversation]` 时，置 `_ending=True` 且**不再发起第二次 LLM 调用**（mock `astream_once` 计数）。
4. `_on_final`：`_ending=True` 时忽略输入（不创建 turn task）。
5. `_on_tts_state` tts_end + `_ending`：发送 `conversation_end` 事件（捕获 `_send` 输出验证）。
6. `ENABLE_END_BY_VOICE=False` 时 `tool_registry` 不含 `end_conversation` schema。

### selftest

跳过（理由见非目标）。

### 浏览器手测清单（验收）

- 说「再见」/「拜拜」→ 助手告别 → 播完自动断连、回初始态。
- 说「再见亦是朋友」（非结束意图）→ 不触发结束。
- speaking 时说「算了不聊了，再见」→ barge-in → 正常结束。
- `ENABLE_END_BY_VOICE=false` → 说「再见」不触发（仅 LLM 文本回应）。

## 风险与边界

- **LLM 漏判/误判**：依赖 system prompt + 模型理解。默认平衡敏感度；用户可调 prompt。
- **告别语被截断**：前端「等播放队列空再关」已避免；后端 30s 兜底防卡死。
- **自定义 prompt**：用户 `.env` 覆盖 `LLM_SYSTEM_PROMPT` 时需自行含引导，否则 LLM 不知有此工具。
- **MCP-only / 视觉关闭**：`end_conversation` 独立于 `ENABLE_VISION`/`ENABLE_MCP`，由 `ENABLE_END_BY_VOICE` 单独控制。
