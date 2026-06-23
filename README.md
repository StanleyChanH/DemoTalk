# DemoTalk · 实时低延迟语音助手（带视觉）

基于 Python 的实时语音对话助手，模型服务全部来自**阿里云百炼（DashScope）**：

| 能力 | 模型 | 接入 |
|---|---|---|
| 语音识别 (STT) | `fun-asr-realtime` | dashscope SDK，全双工 WebSocket |
| 大模型 (LLM) | `qwen3.7-plus` | OpenAI 兼容接口，SSE 流式（关闭思考） |
| 语音合成 (TTS) | `cosyvoice-v3-flash` | dashscope SDK，WebSocket 流式回调 |
| 视觉（多模态） | `qwen3.7-plus` | LLM 主动调用 `take_photo` 工具 → 前端拍照 → 多模态回传 |

- 浏览器前端：打字机流式对话输出、开始/结束对话按钮、调用本机麦克风、扬声器与摄像头。
- 低延迟：STT 实时转写、LLM 流式产出、按句即时喂给 TTS、PCM 直传播放（首包约 350ms）。
- 视觉：对话需要"看"时（如「这是什么」「前面有什么」），LLM 主动调 `take_photo` 拍照，基于画面回答（qwen3.7-plus 多模态）。
- 支持打断（barge-in）：助手说话时你开口，立即停止播报并进入新一轮。
- 工具链：`uv` 管理环境与依赖。

## 架构

```
浏览器麦克风(16kHz/16bit PCM) + 摄像头预览
   │  WebSocket binary（PCM） / 文本事件
   ▼
FastAPI ──► STT(fun-asr-realtime, SDK线程)  ──partial──► 实时字幕
   │                                          └─final─► LLM(qwen3.7-plus, enable_thinking=False)
   │                                                       ├─delta─► 打字机文本 + 按句喂 TTS
   │                                                       │                └─PCM─► 浏览器播放
   │                                                       └─tool_calls(take_photo)─► WS 下发 take_photo
   │                                                                                      └─前端抓帧 → photo 回传
   │                                                                                      └─多模态 tool 结果回 LLM
   │                                                                                           └─delta─► 打字机 + TTS
   └─ barge-in：句末到达且当前在播报 → 取消 TTS + 停止播放
```

线程模型：STT/TTS 回调运行在 dashscope SDK 自有线程，经 `asyncio.run_coroutine_threadsafe` 桥接到事件循环；LLM 用 `AsyncOpenAI` 纯异步。状态机：`listening → thinking → speaking → listening`。

## 前置条件

- **uv**（[安装](https://docs.astral.sh/uv/)）：本机已自带则忽略。
- **Python 3.10–3.13**：uv 会按 `.python-version` 自动下载 3.12。
- **百炼 API Key**：在 [百炼控制台](https://bailian.console.aliyun.com/?tab=model#/api-key) 获取（本项目默认走**北京**地域）。
- 浏览器需支持 `AudioWorklet`（Chrome / Edge / Firefox 新版均可）；首次需允许**麦克风与摄像头**权限（视觉功能需要摄像头）。

## 快速开始

```bash
# 1. 安装依赖（uv 自动建 .venv）
uv sync
# Windows 若报“系统无法打开指定的设备或文件”（Defender 锁文件），改用：
#   UV_LINK_MODE=copy uv sync --no-install-project

# 2. 配置 API Key
cp .env.example .env
#   编辑 .env，填入 DASHSCOPE_API_KEY=sk-xxxx

# 3. 启动
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 若 uv sync 成功安装了项目本体（未用 --no-install-project），也可用：uv run demotalk
```

> 自检（无头往返，用 TTS 合成的语音喂回 STT 验证全链路）：先按上面启动服务，另开终端 `uv run python scripts/selftest.py`，看到 `===== 全部自检通过 =====` 即代表 STT→LLM→TTS 真实打通。

浏览器打开 <http://127.0.0.1:8000>，点「**开始对话**」，允许**麦克风与摄像头**后即可开口交谈；问「我前面这是什么」可触发视觉拍照。

## 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | （必填） | 百炼 API Key |
| `STT_MODEL` | `fun-asr-realtime` | 实时语音识别模型 |
| `LLM_MODEL` | `qwen3.7-plus` | 大模型（混合思考，默认强制关闭思考） |
| `TTS_MODEL` | `cosyvoice-v3-flash` | TTS 模型 |
| `TTS_VOICE` | `longanyang` | 音色（龙安洋）。系统音色还有 `longxiaochun`/`longwan`/`longcheng` 等 |
| `TTS_SAMPLE_RATE` | `24000` | TTS 输出采样率（24000/22050/48000/16000） |
| `LLM_SYSTEM_PROMPT` | 简洁中文助手 | 系统提示词 |
| `LLM_TEMPERATURE` | `0.7` | 采样温度 |
| `MAX_SENTENCE_SILENCE` | `800` | STT 句尾静音毫秒，越小越快判定说完（200–6000） |
| `ENABLE_BARGE_IN` | `true` | 是否允许打断 |
| `ENABLE_VISION` | `true` | 是否启用视觉（take_photo 工具） |
| `PHOTO_MAX_SIZE` | `640` | 拍照最长边像素 |
| `PHOTO_QUALITY` | `0.8` | JPEG 质量 |
| `TAKE_PHOTO_TIMEOUT` | `5` | 拍照超时(秒) |
| `MAX_TOOL_CALLS_PER_TURN` | `3` | 每轮工具调用上限 |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 服务监听 |

### 关于 TTS 模型（重要）

- `cosyvoice-v3-flash`（默认）：支持**系统音色**，开箱即出声，首包延迟约 350ms。
- 若切换到 `cosyvoice-v3.5-flash`：**仅北京区**可用、且**没有任何系统音色**，必须先在百炼做「声音复刻」或「声音设计」拿到一个 `voice id`，填到 `TTS_VOICE`，否则合成会失败。

## 项目结构

```
DemoTalk/
├── pyproject.toml          # uv 依赖与入口
├── .env.example            # 配置模板
├── app/
│   ├── main.py             # FastAPI：静态前端 + WebSocket
│   ├── config.py           # 环境变量配置
│   ├── session.py          # 会话编排：状态机 + 三服务联动 + barge-in + tool 循环
│   ├── stt.py              # fun-asr-realtime 封装
│   ├── llm.py              # qwen3.7-plus 流式（astream_once + tool_calls 检测）
│   ├── tts.py              # cosyvoice 流式封装
│   └── tools/              # 通用 tool 框架（视觉 take_photo；将来 MCP/Skills 复用）
│       ├── base.py         # ToolResult / ToolContext / Tool 协议
│       ├── registry.py     # ToolRegistry 注册表
│       └── builtin/take_photo.py
└── static/
    ├── index.html          # 界面
    ├── style.css           # 样式
    └── app.js              # 麦克风/打字机/播放/WS 逻辑
```

## 视觉能力（摄像头）

`qwen3.7-plus` 是多模态模型。当对话需要"看"时（如用户问「这是什么」「前面有什么」），LLM 会**主动调用** `take_photo` 工具：

1. LLM 发起 `take_photo` → 后端经 WS 通知前端
2. 前端抓取当前摄像头画面（JPEG，最长边默认 640px）→ 回传
3. 后端把图像作为多模态 tool 结果回 LLM → LLM 基于画面回答 → TTS 播报

前置：浏览器与系统均需授权摄像头。点「开始对话」时会同时申请麦克风 + 摄像头，右下角显示预览窗。`ENABLE_VISION=false` 可关闭视觉能力（回退纯语音）。

每轮对话最多调用 3 次工具（`MAX_TOOL_CALLS_PER_TURN`），防止循环。

> 手动验证视觉：启动服务后浏览器打开页面，授权麦克风+摄像头，对镜头展示一个物体并问「我前面这个东西是什么」，应看到"正在拍照…"提示、画面闪光、随后助手基于画面回答并语音播报。

## 常见问题

- **听不到声音**：确认 `TTS_MODEL`+`TTS_VOICE` 匹配（v3.5-flash 必须配自创音色 id）；确认浏览器未静音、页面已获音频播放权限（点过「开始对话」）。
- **麦克风无权限**：浏览器地址栏点击锁形图标，允许麦克风，刷新页面。
- **`未配置 DASHSCOPE_API_KEY`**：`.env` 未创建或 Key 为空。
- **报 401/403**：Key 与地域不匹配（北京用北京 key，国际站用对应 key）。
- **延迟偏高**：调小 `MAX_SENTENCE_SILENCE`（如 600）；LLM 换 `qwen-plus`/`qwen-flash`（更快、默认关思考）。

## 协议说明（WebSocket）

- 客户端 → 服务端：
  - 文本：`{"type":"stop"}` 结束会话；`{"type":"cancel"}` 主动打断；`{"type":"photo","call_id":...,"data":...}` 拍照回传（含 call_id 与 base64 JPEG）；`{"type":"photo_error","call_id":...,"message":...}` 拍照失败。
  - 二进制：麦克风 16kHz/16bit/单声道 PCM。
- 服务端 → 客户端：
  - `tts_format` / `state` / `partial`（实时转写）/ `user_final` / `delta`（助手增量）/ `tts_start` / `tts_end` / `cancel_playback` / `error`；视觉相关：`take_photo`（要求拍照，含 `call_id`）/ `tool_running`（工具执行中，含 `tool` 名）/ `vision_config`（下发拍照参数）。
  - 二进制：TTS 的 PCM（按 `tts_format` 解码）。
