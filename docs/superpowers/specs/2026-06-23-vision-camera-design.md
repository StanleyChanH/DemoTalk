# 视觉/摄像头能力 设计文档

- 日期：2026-06-23
- 状态：待评审
- 子项目：DemoTalk 能力扩展 · 1/3（视觉）
- 关联：后续「MCP 接入」「Skills 接入」将复用本文建立的 tool-calling 框架

## 1. 背景与目标

DemoTalk 当前是纯语音助手：浏览器麦克风 → STT(`fun-asr-realtime`) → LLM(`qwen3.7-plus`) → TTS(`cosyvoice`) → 播放，状态机 `listening → thinking → speaking → listening`。

本子项目为对话增加**视觉能力**：当对话需要「看」时，LLM 主动调用摄像头拍照，基于画面回答。`qwen3.7-plus` 本身是多模态模型。

**目标**：
- LLM 在需要视觉时**自主调用** `take_photo` 工具（非用户手动触发）
- 摄像头**常开 + 前端预览**，拍照低延迟
- 建立**通用 tool-calling 框架**，为后续 MCP / Skills 铺路，避免返工

**非目标（本子项目不做）**：
- MCP 外部 server 接入（另立子项目）
- Skills 系统（另立子项目）
- 持续视频流 / 录像

## 2. 已验证的技术约束

实测 `qwen3.7-plus` 经百炼 OpenAI 兼容接口（`enable_thinking=False`）：

1. ✅ **流式 + tools**：正确发起 `take_photo`（`finish_reason=tool_calls`，`name=take_photo`）
2. ✅ **多模态图像输入**：正确识别测试图（左红右蓝）
3. ✅ **多模态 tool result**（格式1：tool message 多模态 content）：模型基于图正确回答

三个 go/no-go 约束全部通过，方案成立。

## 3. 总体架构

**核心改动**：LLM 调用从「纯文本流式」升级为「通用 tool-calling 循环 + 多模态」。

### 3.1 新增模块 `app/tools/`（通用 tool 框架）

- `base.py` — Tool 协议
  - `schema`：OpenAI function schema（name / description / parameters）
  - `async execute(ctx) -> ToolResult`
  - `ToolResult`：可承载文本或图像（图像 = base64 data URL）
- `registry.py` — 注册表（name → Tool 实例），启动时注册内置 tool
- `builtin/take_photo.py` — 内置拍照 tool

### 3.2 修改 `app/llm.py`

- `astream()` 增加 `tools` 参数
- 新增 tool-calling 循环：
  1. 流式调用，累积 delta（正文）+ tool_calls
  2. 若 `finish_reason=tool_calls`：暂停，yield 已收正文，逐一调用各 tool handler
  3. tool 结果（多模态）追加为 tool message，二次（多次）调用 LLM
  4. 循环直到 `finish_reason=stop` 或达 `MAX_TOOL_CALLS_PER_TURN`

### 3.3 修改 `app/session.py`

- `_run_turn()` 编排 tool 循环
- `take_photo` 执行：经 WS 发 `take_photo` 请求 → 等前端 `photo` 回传（按 `call_id` 匹配，超时 `TAKE_PHOTO_TIMEOUT`）→ 构造多模态 tool result

### 3.4 修改 `static/app.js` + `static/index.html`

- `startSession()` 时 `getUserMedia({audio, video})` 同时开麦克风和摄像头
- 新增 `<video>` 预览窗（右上角小窗 ~160×120，镜像）
- 处理 `take_photo` / `photo` / `photo_error` / `tool_running` 消息
- 拍照：`<video>` 帧 → canvas → resize 最长边 640px → `toDataURL('image/jpeg', 0.8)` → 回传

## 4. 数据流（一次带视觉的对话）

```
用户「我前面红色的东西是什么」→ STT → user_final
→ LLM 流式 (tools=[take_photo])
→ LLM 输出「我看看」+ tool_call(take_photo)     [finish_reason=tool_calls]
→ 后端检测到 tool_call → WS 发 take_photo 请求 (call_id)
→ 前端抓当前帧 → JPEG/base64 → WS 回传 photo (call_id)
→ 后端构造 tool message (多模态图像, 格式1) → 二次调 LLM
→ LLM 流式「这是…」→ delta → TTS → 播放
```

tool 循环持续到 LLM 不再调 tool（`finish_reason=stop`）——支持连续多次拍照。

## 5. WS 消息协议（新增）

| 方向 | type | 载荷 | 说明 |
|---|---|---|---|
| 后端→前端 | `take_photo` | `{call_id}` | 要求前端拍一张 |
| 前端→后端 | `photo` | `{call_id, data}` | `data` = base64 JPEG |
| 前端→后端 | `photo_error` | `{call_id, message}` | 拍照失败（如未授权） |
| 后端→前端 | `tool_running` | `{tool:"take_photo"}` | 前端显示「正在拍照…」 |

## 6. 多模态 tool result 格式（格式1，已验证）

```python
{"role": "tool", "tool_call_id": cid, "content": [
    {"type": "text", "text": "拍到的照片"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
]}
```

## 7. 状态机

**不新增状态**。拍照发生在 `thinking` 阶段（LLM 调 tool 时已是 thinking）。前端靠 `tool_running` 消息显示「正在拍照…」做区分提示。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| 摄像头未授权 | 前端 `describeStartError` 明确提示；对话中拍照失败回 `photo_error`，tool 返回「摄像头不可用」给 LLM |
| 拍照超时（5s） | tool 返回「拍照超时」，LLM 转告用户 |
| LLM 不调 tool | 用户问视觉问题但 LLM 没拍照 → 靠 system prompt 引导；不强制 |
| tool 循环失控 | 每轮最多 3 次 tool 调用，超出强制结束并提示 |
| 图像异常 | 前端控制 JPEG 大小；后端校验 base64 合法性 |

## 9. 配置（`.env` / `app/config.py`，新增）

```
ENABLE_VISION=true           # 是否启用视觉 tool
PHOTO_MAX_SIZE=640           # 拍照最长边像素
PHOTO_QUALITY=0.8            # JPEG 质量
TAKE_PHOTO_TIMEOUT=5         # 拍照超时(秒)
MAX_TOOL_CALLS_PER_TURN=3    # 每轮 tool 调用上限(防失控)
```

同时给 `LLM_SYSTEM_PROMPT` 追加引导：「当需要看用户画面时（如『这是什么』『前面有什么』），先调用 `take_photo` 拍照再回答」。

## 10. 测试策略

- **单元**
  - tool registry 注册 / 查找
  - `take_photo` handler（mock WS：验证 `take_photo` 请求发出、`photo` 按 call_id 匹配、超时分支）
  - llm tool-calling 循环（mock LLM 返回 tool_call，验证循环与多模态回传）
- **集成**：扩展 `scripts/selftest.py` 新增 **Phase 4**——WS 客户端说一句视觉问题 → 收到 `take_photo` → 回传测试图 → 验证 LLM 基于图回答 + TTS 音频返回
- **手动**：浏览器实测（授权摄像头，对着物体提问）

## 11. 将来扩展

`app/tools/` 为通用设计。MCP 子项目接入时：实现一个 `McpTool` 适配器，把 MCP server 的 tools 注册进 `registry`，复用同一 tool-calling 循环。Skills 子项目同理。视觉功能完成后，MCP 子项目大约只剩「连外部 server + 注册其 tools」。

## 12. 开放问题（实现时确认）

- 百炼 tool message 多模态 content 的稳定性（格式1已验证可行，实现时 TDD 锁定）
- JPEG 大小对延迟 / 成本的影响（640px / 0.8 为起点，实测调整）
- `<video>` 预览窗在移动端的适配（本子项目聚焦桌面浏览器）
