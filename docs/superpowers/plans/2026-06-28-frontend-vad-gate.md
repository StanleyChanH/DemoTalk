# 前端 Silero VAD 语音门控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前端接入 Silero VAD 语音门控，只把「检测到人声」的麦克风帧上传给后端 ASR，显著降低背景噪声（键盘、咳嗽、电视音乐、风声等）对对话的误触发。

**Architecture:** 前端用 `@ricky0123/vad-web` 的 `MicVAD` 接管麦克风的 audio track（经 `getStream` 复用既有 `getUserMedia(audio+video)` 流，摄像头预览不变）。`onSpeechStart`→`onSpeechEnd` 之间逐帧把 16kHz Float32 转 Int16 PCM 上传，静音/非人声帧丢弃；pre-roll ring buffer 保头音，`minSpeechMs` 过滤短爆点。silero ONNX 模型 + onnxruntime WASM 本地托管 `static/vad/`（离线可用），库 JS 走 jsdelivr `+esm` 自动打包。后端零改动（PCM 帧格式不变），仅新增 `VAD_SENSITIVITY` 配置经 `config_defaults` 下发；前端灵敏度滑块 0-100 经 `sensitivityToVadOpts` 映射成三阈值，`vad.setOptions()` 实时生效。初始化失败自动回退到原 `MicPcm` 直传路径。

**Tech Stack:** `@ricky0123/vad-web@0.0.30`（Silero VAD ONNX 移植）、`onnxruntime-web`（WASM）、原生 JS AudioWorklet（既有）、FastAPI/Python（仅 config）、pytest（后端 TDD）。

**测试策略：** 后端改动用 pytest TDD；前端项目无 JS 测试框架（沿用既有约定），每个前端任务给出明确的浏览器手动验证步骤。所有测试/验证命令在项目根 `e:\GitProjects\DemoTalk` 执行，pytest 用 `uv run pytest`。

---

## 文件结构

| 文件 | 责任 | 改动类型 |
|---|---|---|
| `app/config.py` | 新增 `VAD_SENSITIVITY` 配置项 | 修改 |
| `app/session.py` | `config_defaults` 下发 `vad_sensitivity` | 修改（1 行） |
| `.env.example` | 文档化 `VAD_SENSITIVITY` | 修改 |
| `tests/test_session_flags.py` | 断言 `config_defaults` 含 `vad_sensitivity` | 修改（TDD） |
| `static/vad/` | 本地 silero ONNX 模型 + ort WASM | 新增（二进制） |
| `static/index.html` | 灵敏度滑块行 + 语音检测指示元素 | 修改 |
| `static/style.css` | 滑块行 + 检测指示样式（暖夜风格） | 修改 |
| `static/app.js` | VAD 接入、帧发送门控、灵敏度状态机、指示、fallback | 修改（核心） |
| `README.md` | `VAD_SENSITIVITY` 配置 + VAD 能力边界说明 | 修改 |

---

## Task 1: 后端 — VAD_SENSITIVITY 配置 + config_defaults 下发

**Files:**
- Modify: `app/config.py`（在 `ENABLE_BARGE_IN` 后新增 VAD 节）
- Modify: `app/session.py:92-100`（`config_defaults` 加字段）
- Modify: `.env.example:27`（对话行为节加 `VAD_SENSITIVITY`）
- Test: `tests/test_session_flags.py:126-136`（`test_start_emits_config_defaults` 加断言）

- [ ] **Step 1: 写失败测试 — 扩展 `test_start_emits_config_defaults`**

在 `tests/test_session_flags.py` 的 `test_start_emits_config_defaults` 末尾（第 136 行 `assert "mcp_available" in cd` 之后）追加一行断言：

```python
    assert cd["vad_sensitivity"] == config.VAD_SENSITIVITY
```

完整的断言区段应为：

```python
    assert cd["barge_in"] == config.ENABLE_BARGE_IN
    assert cd["mcp"] == config.ENABLE_MCP
    assert cd["end_by_voice"] == config.ENABLE_END_BY_VOICE
    assert "mcp_available" in cd
    assert cd["vad_sensitivity"] == config.VAD_SENSITIVITY
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_session_flags.py::test_start_emits_config_defaults -v`
Expected: FAIL（`config_defaults` 不含 `vad_sensitivity`，断言 KeyError 或 config 无该属性）

- [ ] **Step 3: 实现 — `app/config.py` 新增配置项**

在 `app/config.py` 的 `ENABLE_BARGE_IN` 行（第 68 行）之后插入新节：

```python
# ---- 行为 ----
ENABLE_BARGE_IN: bool = _bool("ENABLE_BARGE_IN", True)

# ---- VAD（前端语音门控灵敏度，0-100，越大越灵敏；50=中等）----
VAD_SENSITIVITY: int = _int("VAD_SENSITIVITY", 50)
```

- [ ] **Step 4: 实现 — `app/session.py` 下发字段**

在 `app/session.py` 的 `config_defaults` 消息（约 92-100 行）的 `mcp_available` 字段后追加 `vad_sensitivity`：

```python
        await self._send(
            {
                "type": "config_defaults",
                "barge_in": config.ENABLE_BARGE_IN,
                "mcp": config.ENABLE_MCP,
                "end_by_voice": config.ENABLE_END_BY_VOICE,
                "mcp_available": mcp_manager.has_tools(),
                "vad_sensitivity": config.VAD_SENSITIVITY,
            }
        )
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_session_flags.py::test_start_emits_config_defaults -v`
Expected: PASS

再跑全量回归确保无破坏：
Run: `uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 6: 更新 `.env.example`**

在 `.env.example` 第 27 行 `ENABLE_BARGE_IN=true` 之后插入：

```
# 前端语音门控（VAD）灵敏度 0-100，越大越易触发（挡更多背景噪声则调低）；仅作前端默认值
VAD_SENSITIVITY=50
```

- [ ] **Step 7: 提交**

```bash
git add app/config.py app/session.py .env.example tests/test_session_flags.py
git commit -m "$(cat <<'EOF'
feat(vad): 新增 VAD_SENSITIVITY 配置并经 config_defaults 下发

后端仅下发灵敏度默认值（0-100），VAD 实际运行在前端。
Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 本地 VAD 模型资源（static/vad/）

**Files:**
- Create: `static/vad/silero_vad.onnx`（silero VAD 模型）
- Create: `static/vad/*.wasm`（onnxruntime-web WASM，若干个）

**说明：** `@ricky0123/vad-web` 在浏览器初始化时会 fetch silero ONNX 模型与 onnxruntime WASM。本任务把它们下载到 `static/vad/` 本地托管，使 VAD 离线可用。库 JS 本身走 jsdelivr `+esm`（自动打包依赖），不放入仓库。

- [ ] **Step 1: 创建目录并下载 silero 模型**

```bash
mkdir -p static/vad
curl -L -o static/vad/silero_vad.onnx https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/silero_vad.onnx
```

验证：`ls -la static/vad/silero_vad.onnx`，文件应 ~1.7MB 以上（非 HTML 错误页）。若大小只有几百字节，是 404，检查 URL。

- [ ] **Step 2: 确认 onnxruntime-web 版本并下载 WASM**

先尝试查询 vad-web 0.0.30 依赖的 ort 版本（无 npm 则跳过，默认用 1.17.1）：

```bash
npm view @ricky0123/vad-web@0.0.30 dependencies.onnxruntime-web 2>/dev/null || echo "无 npm，默认用 1.17.1"
```

用得到的版本（设为 `$ORT_VER`，无则 `ORT_VER=1.17.1`）下载 ort wasm（取 dist 下所有 `.wasm` 与 `.mjs`）：

```bash
ORT_VER=1.17.1
BASE="https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VER}/dist"
for f in ort-wasm-simd-threaded.jsep.wasm ort-wasm-threaded.jsep.wasm ort-wasm-simd-threaded.jsep.mjs ort-wasm-threaded.jsep.mjs; do
  curl -L -o "static/vad/$f" "$BASE/$f" || echo "跳过缺失文件: $f"
done
ls -la static/vad/
```

- [ ] **Step 3: 验证文件非空且非错误页**

逐个检查下载文件大小，`.wasm` 应为 MB 级，`.mjs` 为 KB 级，`.onnx` ~1.7MB+。任何几百字节的文件是 404 错误页，需重下或调整版本。

记录实际生效的 `$ORT_VER`，后续 `onnxWASMBasePath` 指向 `/static/vad/` 即可，ort 会按需 fetch 这些文件。

- [ ] **Step 4: 提交二进制资源**

```bash
git add static/vad/
git commit -m "$(cat <<'EOF'
chore(vad): 本地托管 silero VAD 模型与 onnxruntime WASM

使前端 VAD 离线可用；库 JS 仍走 CDN +esm。
Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 前端 — 灵敏度滑块 UI + 状态持久化 + 灵敏度映射函数

**Files:**
- Modify: `static/index.html`（设置面板加滑块行）
- Modify: `static/style.css`（滑块行样式）
- Modify: `static/app.js`（`vad_sensitivity` 状态、`loadFlags/saveFlags/applyDefaults/renderVadSlider`、`sensitivityToVadOpts`、`applyVadSensitivity`、滑块 input 事件）

**说明：** 本任务完成后，滑块可拖动、数值实时显示、localStorage 持久化、`config_defaults` 到达时按「localStorage > .env 默认」初始化。但此时 VAD 实例尚未创建（`vadInstance` 为 `null`），`applyVadSensitivity()` 是空操作——真正生效在 Task 4 接入 VAD 后自动启用。这样拆分保证每个任务自洽、代码无占位符。

- [ ] **Step 1: `static/index.html` — 设置面板加滑块行**

在 `#settingsPanel` 内、最后一个 `.toggle-row`（`data-flag="end_by_voice"`，约第 96-102 行）之后、`.settings-foot`（约第 103 行）之前，插入滑块行：

```html
    <div class="toggle-row slider-row">
      <div class="toggle-info">
        <div class="toggle-label">麦克风灵敏度</div>
        <div class="toggle-desc">值越大越易触发；背景噪声多则调低</div>
      </div>
      <div class="slider-wrap">
        <input id="vadRange" class="vad-range" type="range" min="0" max="100" value="50" aria-label="麦克风灵敏度" />
        <span id="vadRangeVal" class="vad-range-val">50</span>
      </div>
    </div>
```

- [ ] **Step 2: `static/style.css` — 滑块行样式**

在 `.switch:disabled` 规则（约第 551 行）之后、响应式区（约第 553 行 `@media`）之前，追加：

```css
/* 灵敏度滑块行 */
.slider-row { flex-wrap: wrap; }
.slider-wrap { display: inline-flex; align-items: center; gap: 9px; }
.vad-range {
  -webkit-appearance: none; appearance: none;
  width: 110px; height: 5px; border-radius: 999px;
  background: rgba(255,255,255,.14);
  outline: none; cursor: pointer;
}
.vad-range::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 15px; height: 15px; border-radius: 50%;
  background: linear-gradient(135deg, var(--amber-bright), var(--amber-deep));
  box-shadow: 0 0 10px rgba(245,166,83,.5);
  border: none;
}
.vad-range::-moz-range-thumb {
  width: 15px; height: 15px; border-radius: 50%; border: none;
  background: linear-gradient(135deg, var(--amber-bright), var(--amber-deep));
  box-shadow: 0 0 10px rgba(245,166,83,.5);
}
.vad-range-val {
  font-size: 12px; color: var(--amber); font-variant-numeric: tabular-nums;
  min-width: 24px; text-align: right;
}

/* 语音检测指示（默认灰，active 暖琥珀） */
.vad-indicator {
  position: absolute;
  bottom: 14px; right: 14px;
  width: 9px; height: 9px; border-radius: 50%;
  background: rgba(255,255,255,.22);
  box-shadow: 0 0 6px rgba(255,255,255,.18);
  opacity: 0; transition: opacity .3s ease, background .2s, box-shadow .2s;
  z-index: 4;
}
.cam-stage:has(#camView:not(.hidden)) .vad-indicator { opacity: 1; }
.vad-indicator.active {
  background: var(--amber-bright);
  box-shadow: 0 0 12px rgba(245,166,83,.9);
  animation: pulse 1.1s ease-in-out infinite;
}
```

- [ ] **Step 3: `static/app.js` — 扩展 flags 状态与 loadFlags/saveFlags**

把 `static/app.js` 第 42 行的 `flags` 初值加上 `vad_sensitivity`：

```javascript
let flags = { barge_in: true, mcp: true, end_by_voice: true, vad_sensitivity: 50 };
```

扩展 `loadFlags`（第 45-50 行），在 `for` 循环后追加对 `vad_sensitivity`（number）的处理：

```javascript
function loadFlags() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    for (const k of FLAG_KEYS) if (typeof saved[k] === "boolean") flags[k] = saved[k];
    if (typeof saved.vad_sensitivity === "number") flags.vad_sensitivity = saved.vad_sensitivity;
  } catch (e) { /* 损坏则忽略，用默认 */ }
}
```

（`saveFlags` 无需改——它序列化整个 `flags` 对象，已包含 `vad_sensitivity`。）

- [ ] **Step 4: `static/app.js` — 加 DOM 引用、renderVadSlider、sensitivityToVadOpts、applyVadSensitivity**

在 DOM 引用区（约第 14-17 行 `btnCloseSettings`、`settingsPanel`、`toggleRows` 附近）追加：

```javascript
const vadRangeEl = $("#vadRange");
const vadRangeValEl = $("#vadRangeVal");
```

在 `renderToggles` 函数（约第 288 行）之前，新增三个函数：

```javascript
// ---- VAD 灵敏度映射 ----
// s: 0-100，越大越灵敏 → silero 三阈值
function sensitivityToVadOpts(s) {
  const t = Math.max(0, Math.min(100, s)) / 100;
  const positive = 0.75 - 0.45 * t;                  // s=0→0.75（迟钝），s=100→0.30（灵敏）
  const negative = Math.max(0.10, positive - 0.15);  // 滞后防抖
  const minSpeechMs = Math.round(600 - 450 * t);     // s=0→600（严格），s=100→150（宽松）
  return { positiveSpeechThreshold: positive, negativeSpeechThreshold: negative, minSpeechMs };
}

function renderVadSlider() {
  const v = Math.round(flags.vad_sensitivity);
  if (vadRangeEl) vadRangeEl.value = v;
  if (vadRangeValEl) vadRangeValEl.textContent = v;
}

// VAD 实例由 Task 4 创建；此前 applyVadSensitivity 仅更新 cap，接入后 setOptions 才生效
let vadInstance = null;
let vadPreRollCap = 8;  // 候选期头音缓冲容量（帧），按 minSpeechMs 动态更新（silero 每帧≈32ms）
function applyVadSensitivity() {
  const opts = sensitivityToVadOpts(flags.vad_sensitivity);
  vadPreRollCap = Math.max(1, Math.ceil(opts.minSpeechMs / 32));
  if (vadInstance) {
    try { vadInstance.setOptions(opts); } catch (e) {}
  }
}
```

- [ ] **Step 5: `static/app.js` — applyDefaults 处理 vad_sensitivity + 调 renderVadSlider**

修改 `applyDefaults`（约第 307-318 行），在 `mcpAvailable` 赋值后、`renderToggles()` 前插入 `vad_sensitivity` 处理，并在 `renderToggles()` 后加 `renderVadSlider()`：

```javascript
function applyDefaults(defaults) {
  // 字段级优先级：localStorage 上次值 > .env 默认
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}"); } catch (e) { /* 损坏则忽略 */ }
  for (const k of FLAG_KEYS) {
    if (typeof saved[k] === "boolean") flags[k] = saved[k];
    else if (typeof defaults[k] === "boolean") flags[k] = defaults[k];
  }
  // vad_sensitivity：localStorage > .env 默认
  if (typeof saved.vad_sensitivity === "number") flags.vad_sensitivity = saved.vad_sensitivity;
  else if (typeof defaults.vad_sensitivity === "number") flags.vad_sensitivity = defaults.vad_sensitivity;
  mcpAvailable = defaults.mcp_available !== false;
  renderToggles();
  renderVadSlider();
  applyVadSensitivity();
  sendFlags(); // 连接后立即把会话对齐到用户选择
}
```

- [ ] **Step 6: `static/app.js` — 滑块 input 事件 + 初始渲染**

在文件末尾既有 `loadFlags(); renderToggles();`（约第 532-533 行）处，把 `renderVadSlider();` 加入初始渲染：

```javascript
// 初始渲染（未连接时也显示开关，供用户预先设置）
loadFlags();
renderToggles();
renderVadSlider();
```

并在设置面板交互区（`toggleRows.forEach(...)` 之后，约第 529 行后）追加滑块事件绑定：

```javascript
if (vadRangeEl) {
  vadRangeEl.addEventListener("input", () => {
    flags.vad_sensitivity = Number(vadRangeEl.value);
    saveFlags();
    renderVadSlider();
    applyVadSensitivity();
  });
  // 阻止滑块拖动冒泡触发「面板外点击关闭」
  vadRangeEl.addEventListener("click", (e) => e.stopPropagation());
}
```

- [ ] **Step 7: 浏览器手动验证**

启动后端：`uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，打开 http://127.0.0.1:8000 ，点齿轮打开设置面板，验证：

1. 「麦克风灵敏度」滑块出现，初始值 = 50（`.env` 默认，首次无 localStorage）
2. 拖动滑块 → 右侧数值实时变化（0-100）
3. 刷新页面 → 滑块保持上次值（localStorage）
4. 临时改 `.env` 设 `VAD_SENSITIVITY=20`，重启后端，**清除浏览器 localStorage**（DevTools → Application → Local Storage → 删 `demotalk.flags`）→ 刷新 → 滑块初始显示 20（`.env` 默认生效）
5. 验证后把 `.env` 改回 `VAD_SENSITIVITY=50`

此时滑块尚不影响语音识别（VAD 未接入，Task 4 完成）。

- [ ] **Step 8: 提交**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "$(cat <<'EOF'
feat(vad): 前端灵敏度滑块 UI + 状态持久化 + 映射函数

滑块 0-100 实时显示、localStorage 记忆、config_defaults 初始化。
VAD 实例接入前 applyVadSensitivity 为空操作，下个任务生效。
Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 前端 — MicVAD 接入 + 帧发送门控 + 检测指示 + fallback

**Files:**
- Modify: `static/index.html`（摄像头取景区加检测指示元素）
- Modify: `static/app.js`（VAD 模块级状态、`createVad`、`sendVadFrame`、`setVadIndicator`、`startAV`/`stopAV` 改造、`startAVFallback`）

**说明：** 本任务是核心。完成后：开始对话时 VAD 接管麦克风，只在检测到人声时上传 PCM 帧，静音/非人声帧丢弃；灵敏度滑块实时生效；检测指示灯随人声亮灭；VAD 初始化失败自动回退到原 `MicPcm` 直传。`vadInstance` 在 `createVad` 内赋值后，Task 3 的 `applyVadSensitivity` 自动生效，无需再改。

- [ ] **Step 1: `static/index.html` — 检测指示元素**

在 `.cam-stage` 内（与 `.cam-badge` 同级，约第 63 行 `<div class="cam-badge">取景中</div>` 之后）插入：

```html
          <span id="vadIndicator" class="vad-indicator" title="语音检测"></span>
```

- [ ] **Step 2: `static/app.js` — VAD 模块级状态与 DOM 引用**

在视觉状态区（约第 27-32 行 `videoEl`/`flashEl` 附近）追加：

```javascript
// ---- VAD（语音活动检测）运行态 ----
let vadSending = false;      // onSpeechRealStart→true / onSpeechEnd→false
let vadPreRoll = [];         // 候选期头音缓冲（onSpeechRealStart 时 flush；misfire 时丢弃）
const vadIndicatorEl = $("#vadIndicator");
```

（`vadInstance` 已在 Task 3 声明为模块级 `let vadInstance = null;`，此处不重复声明。）

- [ ] **Step 3: `static/app.js` — sendVadFrame 与 setVadIndicator**

在 Task 3 新增的 `applyVadSensitivity` 函数之后，新增帧发送与指示函数：

```javascript
// ---- VAD 帧发送 + 指示 ----
function sendVadFrame(frame) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const i16 = new Int16Array(frame.length);
  for (let i = 0; i < frame.length; i++) {
    let s = Math.max(-1, Math.min(1, frame[i]));
    i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  ws.send(i16.buffer);
}

function setVadIndicator(on) {
  if (vadIndicatorEl) vadIndicatorEl.classList.toggle("active", on);
}
```

- [ ] **Step 4: `static/app.js` — createVad（动态 import + MicVAD.new + 回调接线）**

在 `sendVadFrame` 之后新增 `createVad`。库走 jsdelivr `+esm`（自动打包依赖），模型/ort wasm 指向本地 `/static/vad/`：

```javascript
async function createVad(stream) {
  const mod = await import("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/+esm");
  const MicVAD = mod.MicVAD || mod.default?.MicVAD || mod.default;
  const initOpts = sensitivityToVadOpts(flags.vad_sensitivity);
  vadPreRollCap = Math.max(1, Math.ceil(initOpts.minSpeechMs / 32));
  const vad = await MicVAD.new({
    getStream: async () => stream,
    baseAssetPath: "/static/vad/",
    onnxWASMBasePath: "/static/vad/",
    ...initOpts,
    onSpeechStart: () => {
      // 进入候选：重置缓冲，开始积累（由 onFrameProcessed 的 else 分支）
      vadPreRoll = [];
    },
    onSpeechRealStart: () => {
      // 确认达到 minSpeechMs：flush 完整头音后开始逐帧上传。
      // 短爆点（< minSpeechMs）不会到这，从而被过滤。
      vadSending = true;
      for (const f of vadPreRoll) sendVadFrame(f);
      vadPreRoll = [];
      setVadIndicator(true);
    },
    onFrameProcessed: (_probs, frame) => {
      if (vadSending) {
        sendVadFrame(frame);
      } else {
        // 候选期：积累帧（容量 = minSpeechFrames），超容量丢最旧
        vadPreRoll.push(frame);
        while (vadPreRoll.length > vadPreRollCap) vadPreRoll.shift();
      }
    },
    onSpeechEnd: () => {
      vadSending = false;
      setVadIndicator(false);
    },
    onVADMisfire: () => {
      // 短于 minSpeechMs 的段：丢弃缓冲，不上传
      vadPreRoll = [];
    },
  });
  return vad;
}
```

- [ ] **Step 5: `static/app.js` — startAVFallback（抽出原 MicPcm 直传逻辑）**

在 `startAV`（约第 380 行）之前新增 `startAVFallback`，封装原有的 `micCtx + workletNode` 直传路径（`WORKLET_CODE`/`WORKLET_URL` 不变，仍保留在文件中）：

```javascript
// fallback：VAD 不可用时，回退到无门控的 MicPcm 直传
async function startAVFallback(stream) {
  micCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule(WORKLET_URL);
  const srcNode = micCtx.createMediaStreamSource(stream);
  workletNode = new AudioWorkletNode(micCtx, "mic-pcm");
  workletNode.port.onmessage = (ev) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(ev.data);
  };
  srcNode.connect(workletNode);
  workletNode.connect(micCtx.destination);
}
```

- [ ] **Step 6: `static/app.js` — 改造 startAV（优先 VAD，失败 fallback）**

替换 `startAV`（约第 380-407 行）为下面版本——`getUserMedia` 不变；视频预览不动；音频链路改为「优先 createVad，catch 回退 startAVFallback」：

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
  // 视频预览（不变）
  camStream = micStream;
  videoEl.srcObject = micStream;
  videoEl.classList.remove("hidden");
  await videoEl.play().catch(() => {});
  // 音频链路：优先 VAD 门控；初始化失败回退 MicPcm 直传
  try {
    setHint("VAD 加载中…");
    vadInstance = await createVad(micStream);
    vadInstance.start();
    setHint("麦克风与摄像头已就绪，可以开口说话了");
  } catch (e) {
    console.warn("VAD 初始化失败，回退直传", e);
    vadInstance = null;
    setVadIndicator(false);
    await startAVFallback(micStream);
    setHint("VAD 不可用，已回退直传模式");
  }
}
```

- [ ] **Step 7: `static/app.js` — 改造 stopAV（销毁 VAD）**

替换 `stopAV`（约第 409-419 行）为下面版本——新增 `vadInstance.pause()` 与 VAD 状态复位，其余 MicPcm 清理保留（fallback 路径仍需）：

```javascript
function stopAV() {
  if (vadInstance) { try { vadInstance.pause(); } catch (e) {} vadInstance = null; }
  vadSending = false;
  vadPreRoll = [];
  setVadIndicator(false);
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

- [ ] **Step 8: 浏览器手动验证（核心场景）**

启动后端，打开页面，点「开始对话」，授权麦克风+摄像头。验证：

1. **正常说话**：对麦克风说话 → 助手正常识别、回应（体验不劣化）。说话时摄像头取景区右下角的语音检测圆点应高亮（暖琥珀 + 脉冲），停顿时复位。
2. **背景噪声不误触发**：保持安静，制造非人声背景噪声（敲键盘、咳嗽、拍桌子、放纯音乐）→ 转写区不出现文字、助手不回应。检测圆点保持暗。
3. **灵敏度实时生效**：打开设置面板，把灵敏度拖到很低（如 10）→ 同样强度的说话可能不再触发（更难识别）；拖回 50-70 → 恢复正常。拖到很高（如 90）→ 更易触发（可能连一些噪声也过）。
4. **fallback**：临时把 `static/vad/silero_vad.onnx` 重命名（如加 `.bak`），刷新重连 → 底部提示「VAD 不可用，已回退直传模式」，语音仍可用（但回到无门控状态）；验证后改回文件名。
5. **DevTools 观察**：Network 面板应看到对 `/static/vad/silero_vad.onnx` 与 `/static/vad/*.wasm` 的请求（200）；Console 无报错。若 ort 报找不到某 wasm（404），按报错的文件名补下到 `static/vad/`（参考 Task 2 Step 2）。
6. **采样率排查（兜底）**：vad-web 的 `onFrameProcessed` 的 `frame` 应为 16kHz（silero 模型输入）。若说话识别不出/变调，临时在 `onFrameProcessed` 加 `console.log(frame.length)`：≈512 即 16kHz（正确，直接发）；≈1536 则是 48kHz，需在 `sendVadFrame` 前降采样到 16kHz（vad-web 默认应为 16kHz，此为兜底）。

- [ ] **Step 9: 提交**

```bash
git add static/index.html static/app.js
git commit -m "$(cat <<'EOF'
feat(vad): 接入 MicVAD 语音门控，仅上传人声帧

onSpeechStart/End 状态机门控逐帧上传 + pre-roll 保头音；
本地模型/wasm 加载，初始化失败回退 MicPcm 直传；
检测指示随人声亮灭；灵敏度滑块经 setOptions 实时生效。
Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: README 文档

**Files:**
- Modify: `README.md`（配置表加 `VAD_SENSITIVITY`；加 VAD 说明节）

- [ ] **Step 1: `README.md` — 配置表加 VAD_SENSITIVITY 行**

在 `README.md` 配置表 `ENABLE_BARGE_IN` 行（约第 78 行）之后插入一行：

```markdown
| `VAD_SENSITIVITY` | `50` | 前端语音门控灵敏度 0-100，越大越易触发（背景噪声多则调低） |
```

- [ ] **Step 2: `README.md` — 在「运行时开关（前端）」节后追加 VAD 说明**

在「运行时开关（前端）」节（约第 89-91 行）之后，新增一节：

```markdown
### 语音门控（VAD）

前端接入 Silero VAD（`@ricky0123/vad-web`）：只有检测到**人声**的麦克风帧才上传给 ASR，静音与非人声噪声（键盘、咳嗽、电视音乐、风声等）被丢弃，显著降低背景噪声对对话的干扰。模型本地托管（`static/vad/`），离线可用；库初始化失败时自动回退到无门控直传。

灵敏度滑块（设置面板，0-100，默认取 `VAD_SENSITIVITY`）经 `vad.setOptions()` 实时生效，localStorage 记住上次选择。

**能力边界：** VAD 能区分「人声 vs 非人声噪声」，但**无法区分说话人来源**——环境里的**其他人声**（旁人说话、电视/视频里的人声）仍可能被识别并上传。这是开放式麦克风的固有限制；如需彻底屏蔽环境人声，需引入按住说话（Push-to-Talk）或唤醒词（当前范围外）。
```

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: 补充 VAD 语音门控说明与能力边界
Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## 验收对照（spec → task）

| spec 验收标准 | 实现任务 |
|---|---|
| 1. 安静说话正常识别、不劣化 | Task 4 Step 8-1 |
| 2. 背景噪声不再误触发 | Task 4 Step 8-2 |
| 3. 灵敏度滑块实时生效 | Task 3（UI/状态）+ Task 4 Step 8-3（联动） |
| 4. 刷新滑块保持（localStorage） | Task 3 Step 7-3 |
| 5. 改 `.env` 默认值回显 | Task 3 Step 7-4 |
| 6. 模型缺失/断网 → fallback + 提示 | Task 4 Step 8-4 |
| 7. 检测指示人声高亮/复位 | Task 4 Step 8-1 |
| 8. 全部 pytest 通过 | Task 1 Step 5 |
