# 前端 Silero VAD 语音门控设计

日期：2026-06-28

## 背景与目标

DemoTalk 当前把麦克风 PCM **无差别**全量上传给 DashScope `fun-asr-realtime`：前端 AudioWorklet 每 ~100ms 产出一帧，`workletNode.port.onmessage` 里 `ws.send` 每一帧，后端全量喂 ASR。浏览器虽开了 `noiseSuppression`，但它只抑制**稳态噪声**（风扇/空调嗡嗡声），对两类干扰无能为力：

1. **非稳态低能量背景声**（键盘、咳嗽、关门、远处窸窣声）——浏览器降噪不处理，ASR 却可能当语音
2. **环境人声**（旁人说话、电视/视频声）——无法区分是不是用户在说

ASR 服务端 VAD（`max_sentence_silence=800ms`）只管句尾静音断句，**来者不拒**。结果：背景稍有动静就被转写 → 送 LLM → 助手对着背景"自言自语"。

**目标**：在前端麦克风链路加入 Silero VAD（基于 `@ricky0123/vad-web`），只有检测到人声的帧才上传，静音/非人声帧丢弃。保持"开口即说"的自然交互，把误触发显著降低。灵敏度前端可调（滑块），`.env` 作默认值。

**能力边界**：Silero VAD 对**非人声噪声**（键盘、咳嗽、电视音乐、风声）过滤效果显著——这能挡住浏览器 `noiseSuppression` 漏掉的多数底噪；对**环境人声**（旁人/电视里的人说话）非根治（VAD 分不清"用户的嘴"和"环境里的嘴"），但配合概率阈值 + 最短语音时长能减少误触发。整体相对现状（无任何门控）是显著改善。

## 范围

**包含：**

- 前端接入 `@ricky0123/vad-web`（MicVAD），取代现有 `MicPcm` AudioWorklet 的上传职责
- 流式帧发送门控：说话期间帧上传，静音/非人声帧丢弃
- 灵敏度滑块（0-100）+ `.env` 默认值 + localStorage 记忆
- 语音检测可视化指示
- 本地托管 Silero ONNX 模型 + onnxruntime WASM（离线可用）
- 初始化失败时回退到现有直传模式

**不包含：**

- 不改动后端 STT/LLM/TTS 核心链路（PCM 帧格式不变，后端零改动）
- 不引入 Push-to-Talk / 唤醒词（本次为纯被动 VAD 方案）
- 不新增 HTTP 端点
- 不做环境人声的声纹/说话人分离（超出范围）

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 方案方向 | **前端 ML VAD**（Silero） | 用户选择；对非人声噪声过滤远强于能量门控，保持自然开口即说 |
| VAD 库 | **`@ricky0123/vad-web`** | 基于 Silero ONNX 的成熟前端库，支持流式 `onFrameProcessed` + `setOptions` 运行时调参 + 自定义 `getStream` |
| 模型托管 | **本地托管**（`static/vad/`） | 离线可用，符合 DemoTalk 本地语音助手定位 |
| 灵敏度控件 | **滑块 0-100** | 用户选择；连续精细可调，`setOptions` 实时生效 |
| 灵敏度下发 | **仅 `config_defaults` 下发默认；切换纯前端** | VAD 是前端行为，用户拖滑块本地 `setOptions` 即可，无需回传后端 |
| 检测指示 | **要** | 直观看到 VAD 工作状态与过滤时机 |

## 设计

### 1. 核心架构：音频链路改造

**当前链路：**

```
getUserMedia(audio+video) → AudioContext(16kHz) → MicPcm worklet → ws.send(每帧无差别)
                              └─ video track → <video> 预览
```

**改造后链路：**

```
getUserMedia(audio+video) → MicVAD(经 getStream 接管 audio track，内部 16kHz + Silero)
                              ├─ onFrameProcessed(仅语音帧) → 转 Int16 → ws.send
                              └─ video track → <video> 预览（不变）
```

要点：

- **复用同一 `getUserMedia(audio+video)` 流**：通过 MicVAD 的 `getStream: () => Promise<MediaStream>` 选项把已有流传给 MicVAD（它只用 audio track），video track 继续给 `<video>` 预览。一次 `getUserMedia`，零冗余，摄像头预览完全不变。
- **MicVAD 接管音频处理**：它内部管理 AudioContext + AudioWorklet + Web Worker + ONNX 推理，并把音频重采样到 16kHz 喂给 Silero 模型。现有 `MicPcm` AudioWorklet 的上传职责被取代。
- **`onFrameProcessed` 的 `frame` 是 16kHz Float32 单声道**（vad-web 内部已重采样至模型输入采样率），转 Int16 后与现有后端 PCM 格式（16kHz/16bit/单声道）完全一致 → **后端 `feed_mic` / `stt.feed` 零改动**。
- **实现注意**：在实现阶段需验证 `onFrameProcessed` 的 `frame` 采样率确为 16kHz；若非，则在发送前做一次重采样。同时规避 vad-web 已知 issue #144（某些自定义 worklet 链下 `onFrameProcessed` 不触发）——做法是**只用库的标准 `getStream` 传入 stream，不自行拼装 `MediaStreamAudioSourceNode` + 自定义 worklet**。

### 2. 帧发送策略（关键）

用 MicVAD 自带状态机门控逐帧发送，保证 ASR 时序连续：

- 维护 `sending: boolean` 标志
- `onSpeechStart` → `sending = true`
- `onFrameProcessed(probs, frame)` → 若 `sending`，把 `frame`（Float32）转 Int16 后 `ws.send`
- `onSpeechEnd` → `sending = false`

**为什么这样能解决问题：**

- **说话期间**所有帧（含 VAD 自身 padding）连续发送 → ASR 收到连续人声，时序正常，内部 `max_sentence_silence` 断句逻辑不受干扰
- **静音 / 非人声期**完全不发 → ASR 收不到音频，不会把背景噪声转写成文字 → 不再误触发 LLM

**保头音（pre-roll）**：`onSpeechStart` 是在检测到语音后才触发的，开头几帧（~30-60ms）此时已过 `onFrameProcessed` 但 `sending` 还是 false，会被漏掉。用一个小的 **ring buffer** 缓冲最近 N 帧（N=2-3），`onSpeechStart` 时先把 buffer 里的 pre-roll 帧补发，再置 `sending=true`，避免吃掉字头。

**过滤短爆点**：`minSpeechMs`（最短语音时长）设 300ms。咳嗽、敲击等短噪声达不到该时长，`onSpeechEnd` 不会触发（库语义：短于 `minSpeechMs` 的片段不产生 `onSpeechEnd`，可选触发 `onVADMisfire`）→ 不会开启 `sending` → 不上传。

### 3. 灵敏度配置

**单一 0-100 滑块**，前端映射成 Silero 的三个阈值，调 `vad.setOptions()` 实时生效（`setOptions` 支持运行时调整，无需重建实例）。

**`.env` 配置项**（`app/config.py`）：

- `VAD_SENSITIVITY`：整数 0-100，默认 50。作为前端滑块的初始默认值。

**前端映射函数**（`static/app.js`，越大越灵敏）：

```javascript
// s: 0-100
function sensitivityToVadOpts(s) {
  const t = s / 100;                                   // 0..1
  const positive = 0.75 - 0.45 * t;                    // s=0 → 0.75（迟钝），s=100 → 0.30（灵敏）
  const negative = Math.max(0.10, positive - 0.15);    // 保持滞后，防抖
  const minSpeechMs = Math.round(600 - 450 * t);       // s=0 → 600（严格），s=100 → 150（宽松）
  return { positiveSpeechThreshold: positive, negativeSpeechThreshold: negative, minSpeechMs };
}
```

**协议**（与三开关一致的范式，但更简单——VAD 纯前端）：

| 方向 | type | 时机 | 字段 |
|---|---|---|---|
| 后端→前端 | `config_defaults`（扩展现有） | WS 连接后 `start()` 内 | 新增 `vad_sensitivity`（int 0-100，= `.env` 值） |
| 前端→后端 | **无**（纯前端） | — | 用户拖滑块 → 本地 `setOptions` + localStorage，不回传 |

**优先级**：`localStorage 上次值` > `.env 默认（config_defaults 下发）`。

**VAD 实例与滑块的时序**：

- 会话开始 `startAV()` 时创建 MicVAD，初始 options 用当前灵敏度（localStorage 值 > .env 默认）
- 会话中拖滑块 → `vad.setOptions(sensitivityToVadOpts(newVal))` 实时生效
- 会话未连接时也能拖滑块（设置面板始终可用），只写 localStorage；下次会话创建 MicVAD 时读用

**localStorage 存储**：并入现有 `demotalk.flags` JSON，新增 `vad_sensitivity`（number）字段。`loadFlags` / `saveFlags` 对该字段用 `typeof === "number"` 守卫（区别于三开关的 boolean）。

### 4. 后端改造（极小）

- **`app/config.py`**：新增 `VAD_SENSITIVITY: int = _int("VAD_SENSITIVITY", 50)`
- **`app/session.py`**：`start()` 的 `config_defaults` 消息增加 `"vad_sensitivity": config.VAD_SENSITIVITY`
- 后端无其他改动。VAD 阈值映射、`setOptions`、帧发送全在前端。

### 5. 前端改造

**`static/app.js`**（不破坏现有 WS / TTS 播放 / 打字机 / 三开关逻辑）：

- 引入 `@ricky0123/vad-web`（通过 ESM import 或本地脚本）。模型/ORT 路径指向 `static/vad/`。
- 新增 `createVad(stream)`：`MicVAD.new({ getStream, baseAssetPath: "/static/vad/", onnxWASMBasePath: "/static/vad/", onSpeechStart, onFrameProcessed, onSpeechEnd, ...sensitivityToVadOpts(sensitivity) })`
- `startAV()` 改造：`getUserMedia` 后，先拿 video track 做预览（同现状），audio 链路改走 `createVad(stream)`；正常路径不再创建 `micCtx` / `MicPcm worklet`，其代码保留，**仅在 fallback 分支**中创建并启用。
- `onFrameProcessed` 发送逻辑 + pre-roll ring buffer + Int16 转换。
- `stopAV()` 改造：增加 `vad.pause()` / 销毁。
- 灵敏度状态：`flags.vad_sensitivity`（number）、滑块渲染、`setOptions` 调用、localStorage 持久化。
- 检测指示：`onSpeechStart` → 高亮指示元素；`onSpeechEnd` → 复位。
- Fallback：`createVad` 抛错（模型加载失败 / ORT 初始化失败 / 不支持 AudioWorklet）→ catch，回退到原 `MicPcm` 直传路径，`setHint("VAD 不可用，已回退直传模式")`。

**`static/index.html`**：

- 设置面板新增"麦克风灵敏度"滑块行（`<input type="range" min="0" max="100">` + 数值显示 + 描述）
- 新增"语音检测"指示元素（如摄像头取景区内的圆点 / statePill 旁的灯）

**`static/style.css`**：

- 滑块行样式（与现有 `.toggle-row` 协调的暖夜风格）
- 检测指示样式（默认灰，检测到人声时暖琥珀高亮 + 微动效）

**`static/vad/`**（新增目录，二进制）：

- Silero VAD ONNX 模型文件
- onnxruntime-web WASM 文件及相关 `.wasm`
- 这些文件由 `@ricky0123/vad-web` 与 `onnxruntime-web` 的发布物提供，构建/拷贝进 `static/vad/`（仓库体积 +~3-5MB）

### 6. 边缘情况与测试

**边缘情况（均已覆盖）：**

1. **VAD 误判静音为语音**（漏过滤）→ 调低灵敏度（提高阈值）
2. **VAD 误判语音为静音**（吃字）→ 调高灵敏度 + pre-roll buffer 保头音
3. **浏览器不支持 AudioWorklet / ORT 初始化失败 / 模型加载失败** → catch 回退到 `MicPcm` 直传 + 提示
4. **`onFrameProcessed` 不触发（issue #144）** → 只用库标准 `getStream`，不自拼 worklet 链
5. **说话中途短停顿（喘气）** → VAD `negativeSpeechThreshold` + 内部 redemption 保持"说话中"，帧连续发送，ASR 时序不断
6. **barge-in 期间** → VAD 检测到新人声 → 上传 → ASR final → 触发 barge-in，与现有逻辑无缝衔接
7. **会话启停** → `startAV` 创建并 `vad.start()`，`stopAV` 里 `vad.pause()` / 销毁，与现有生命周期整合
8. **模型首次加载延迟** → 创建期间显示"VAD 加载中…"提示
9. **滑块在未连接时拖动** → 只写 localStorage，下次会话创建 MicVAD 时生效
10. **localStorage 损坏/非数字** → `typeof === "number"` 守卫失败，回退 `.env` 默认

**测试（pytest，沿用现有 `tests/` 结构）：**

- `tests/test_session_flags.py`（扩展）：断言 `start()` 下发的 `config_defaults` 含 `vad_sensitivity` 字段且 = `config.VAD_SENSITIVITY`
- 可选新增 `tests/test_config.py`（若尚无）：断言 `VAD_SENSITIVITY` 读取（默认 50、环境变量覆盖）
- **前端**：项目无 JS 测试框架，沿用现有做法**手动验证**——见验收标准

## 文件清单

| 文件 | 改动 |
|---|---|
| `static/app.js` | 接入 MicVAD 取代 MicPcm 上传；帧发送门控 + pre-roll；灵敏度滑块状态机；检测指示；fallback |
| `static/index.html` | 灵敏度滑块行；语音检测指示元素 |
| `static/style.css` | 滑块 + 检测指示样式（暖夜风格） |
| `static/vad/` | 新增：本地 Silero ONNX 模型 + onnxruntime WASM（二进制，~3-5MB） |
| `app/config.py` | 新增 `VAD_SENSITIVITY` 配置项 |
| `app/session.py` | `config_defaults` 下发 `vad_sensitivity` |
| `tests/test_session_flags.py` | 扩展：`config_defaults` 含 vad 字段 |
| `README.md` | 补充 VAD 说明 + `.env` 配置（可选） |

## 验收标准

1. 浏览器打开页面，开始对话后**安静说话** → 正常识别、正常回应，体验不劣化
2. 安静期间制造**背景噪声**（敲键盘、放音乐、咳嗽、远处窸窣声）→ ASR 不再误触发，助手不会对着背景自言自语
3. 灵敏度滑块实时生效：拖动后下一句话即可观察到灵敏度变化（更易/更难触发）
4. 刷新页面，滑块保持上次选择（localStorage）
5. 改 `.env` 的 `VAD_SENSITIVITY` 后，无 localStorage 的滑块显示新默认值（localStorage 优先于 `.env`）
6. 删除 `static/vad/` 模型文件或断网导致加载失败 → 自动回退到直传模式 + 前端提示"VAD 不可用，已回退"
7. 检测指示在 VAD 检测到人声时高亮、静音时复位
8. 全部 pytest 通过

## 已知局限（需向用户说明）

- **环境人声**（旁人说话、电视/视频里的人声）仍可能被识别为语音并上传——这是开放式麦克风的根本难题，VAD 无法区分说话人来源。本次方案对此为"缓解"非"根治"。如未来需根治，需引入 Push-to-Talk 或唤醒词（本次范围外）。
- Silero VAD 引入 ~3-5MB 本地模型 + WASM，首次创建有数十~数百 ms 加载延迟。
