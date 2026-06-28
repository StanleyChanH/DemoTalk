// DemoTalk 前端：WebSocket 双向通信 + 麦克风采集 + 打字机 + TTS 播放 + barge-in
"use strict";

const $ = (sel) => document.querySelector(sel);

// ---- DOM ----
const transcriptEl = $("#transcript");
const partialEl = $("#partial");
const statePill = $("#statePill");
const connPill = $("#connPill");
const btnStart = $("#btnStart");
const btnStop = $("#btnStop");
const hintEl = $("#hint");
const btnSettings = $("#btnSettings");
const btnCloseSettings = $("#btnCloseSettings");
const settingsPanel = $("#settingsPanel");
const toggleRows = settingsPanel.querySelectorAll(".toggle-row:not(.slider-row)");
const vadRangeEl = $("#vadRange");
const vadRangeValEl = $("#vadRangeVal");

// ---- 状态 ----
let ws = null;
let micCtx = null;
let micStream = null;
let workletNode = null;
let playCtx = null;
let ttsSampleRate = 24000; // 由后端 tts_format 消息告知

// ---- 视觉（摄像头预览 / 闪光 / 拍照参数）----
const videoEl = $("#camView");
const flashEl = $("#flash");
let camStream = null;
let photoMaxSize = 640;
let photoQuality = 0.8;

// ---- VAD（语音活动检测）运行态 ----
let vadSending = false;      // onSpeechRealStart→true / onSpeechEnd→false
let suppressPartial = false;   // speaking 期间隐藏 partial，避免回声转写在字幕区闪烁
let vadPreRoll = [];         // 候选期头音缓冲（onSpeechRealStart 时 flush；misfire 时丢弃）
const vadIndicatorEl = $("#vadIndicator");

// 播放调度
let nextStart = 0;
let sources = [];
let endingByVoice = false;

// ---- 功能开关（中断 / MCP / 语义结束）----
const FLAG_KEYS = ["barge_in", "mcp", "end_by_voice"];
const LS_KEY = "demotalk.flags";
let flags = { barge_in: true, mcp: true, end_by_voice: true, vad_sensitivity: 50 };
let mcpAvailable = true;

function loadFlags() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    for (const k of FLAG_KEYS) if (typeof saved[k] === "boolean") flags[k] = saved[k];
    if (typeof saved.vad_sensitivity === "number") flags.vad_sensitivity = saved.vad_sensitivity;
  } catch (e) { /* 损坏则忽略，用默认 */ }
}
function saveFlags() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(flags)); } catch (e) {}
}

// 打字机
let asstEl = null;      // 当前助手气泡 DOM
let asstBuffer = "";    // 当前助手气泡的完整文本
let asstShown = 0;      // 已显示字符数
let twRAF = null;

const STATE_TEXT = { idle: "待机", listening: "聆听中", thinking: "思考中", speaking: "播报中" };

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

// ---- 麦克风 AudioWorklet：输出 16kHz/16bit/单声道 PCM（~100ms/帧）----
const WORKLET_CODE = `
class MicPcm extends AudioWorkletProcessor {
  constructor() {
    super();
    this._frames = [];
    this._count = 0;
    this._target = 1600; // 100ms @16kHz
  }
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const ch = input[0];
      const copy = new Float32Array(ch.length);
      copy.set(ch);
      this._frames.push(copy);
      this._count += copy.length;
      if (this._count >= this._target) {
        const merged = new Float32Array(this._count);
        let o = 0;
        for (const f of this._frames) { merged.set(f, o); o += f.length; }
        const i16 = new Int16Array(merged.length);
        for (let i = 0; i < merged.length; i++) {
          let s = Math.max(-1, Math.min(1, merged[i]));
          i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        this.port.postMessage(i16.buffer, [i16.buffer]);
        this._frames = [];
        this._count = 0;
      }
    }
    return true;
  }
}
registerProcessor("mic-pcm", MicPcm);
`;
const WORKLET_URL = URL.createObjectURL(new Blob([WORKLET_CODE], { type: "application/javascript" }));

// ---- 状态显示 ----
function setConn(state) {
  connPill.textContent = state ? "已连接" : "未连接";
  connPill.className = "pill " + (state ? "pill-live" : "pill-off");
}
function setState(state) {
  statePill.textContent = STATE_TEXT[state] || state;
  statePill.className = "pill " + (state || "");
}
function setHint(text) {
  hintEl.textContent = text || "";
}

// ---- 气泡 ----
function addBubble(role, text = "") {
  // 移除欢迎语
  const welcome = $("#welcome");
  if (welcome) welcome.remove();

  const el = document.createElement("div");
  el.className = "msg " + role;
  const roleEl = document.createElement("span");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? "你" : "助手";
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = text;
  el.appendChild(roleEl);
  el.appendChild(body);
  transcriptEl.appendChild(el);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return el;
}

function addError(text) {
  const el = document.createElement("div");
  el.className = "msg error";
  el.textContent = text;
  transcriptEl.appendChild(el);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// ---- 打字机 ----
function startAssistantBubble() {
  asstEl = addBubble("assistant");
  asstEl.querySelector(".body").classList.add("cursor");
  asstBuffer = "";
  asstShown = 0;
  if (!twRAF) twRAF = requestAnimationFrame(twTick);
}

function twTick() {
  if (asstEl && asstShown < asstBuffer.length) {
    // 落后越多步长越大；基本恒速但能追上
    const behind = asstBuffer.length - asstShown;
    const step = Math.max(1, Math.round(behind / 10));
    asstShown = Math.min(asstBuffer.length, asstShown + step);
    asstEl.querySelector(".body").textContent = asstBuffer.slice(0, asstShown);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
    twRAF = requestAnimationFrame(twTick);
  } else {
    twRAF = null;
  }
}

function flushTypewriter() {
  if (twRAF) { cancelAnimationFrame(twRAF); twRAF = null; }
  if (asstEl) {
    asstShown = asstBuffer.length;
    asstEl.querySelector(".body").textContent = asstBuffer;
    asstEl.querySelector(".body").classList.remove("cursor");
  }
  asstEl = null;
}

// ---- TTS 播放 ----
function ensurePlayCtx() {
  if (!playCtx) playCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (playCtx.state === "suspended") playCtx.resume();
  return playCtx;
}

function playPcm(arrayBuffer) {
  const ctx = ensurePlayCtx();
  const i16 = new Int16Array(arrayBuffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
  const buf = ctx.createBuffer(1, f32.length, ttsSampleRate); // Web Audio 自动重采样到设备采样率
  buf.copyToChannel(f32, 0);
  const node = ctx.createBufferSource();
  node.buffer = buf;
  node.connect(ctx.destination);
  const now = ctx.currentTime;
  if (nextStart < now) nextStart = now;
  try {
    node.start(nextStart);
  } catch (e) {
    return;
  }
  nextStart = nextStart + buf.duration;
  sources.push(node);
  node.onended = () => {
    sources = sources.filter((s) => s !== node);
    // 语义结束：告别语播完（队列空）再断连，避免截断尾音
    if (endingByVoice && sources.length === 0) stopSession();
  };
}

function stopPlayback() {
  for (const s of sources) {
    try { s.stop(); } catch (e) {}
  }
  sources = [];
  const ctx = playCtx;
  nextStart = ctx ? ctx.currentTime : 0;
}

// ---- WebSocket ----
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    setConn(true);
    setState("listening");
    setHint("正在请求麦克风…");
  };
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      playPcm(ev.data);
      return;
    }
    let obj;
    try { obj = JSON.parse(ev.data); } catch (e) { return; }
    handleEvent(obj);
  };
  ws.onclose = () => {
    setConn(false);
    endingByVoice = false;  // 连接关闭：清除语音结束标志，防跨会话泄漏
    setState("idle");
    stopAV();
    stopPlayback();
    btnStart.disabled = false;
    btnStop.disabled = true;
    setHint("连接已断开");
  };
  ws.onerror = () => { setHint("连接出错"); };
}

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

// ---- createVad：动态 import + MicVAD.new + 回调接线 ----
async function createVad(stream) {
  const mod = await import("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/+esm");
  const MicVAD = mod.MicVAD || mod.default?.MicVAD || mod.default;
  const initOpts = sensitivityToVadOpts(flags.vad_sensitivity);
  vadPreRollCap = Math.max(1, Math.ceil(initOpts.minSpeechMs / 32));
  const vad = await MicVAD.new({
    getStream: async () => stream,
    baseAssetPath: "/static/vad/",
    // onnxWASMBasePath 必须用绝对 URL：ort 的 .mjs loader 随 vad-web 从 CDN 加载，
    // 内部用 new URL(path, import.meta.url) 解析 wasm 路径——若给相对 "/static/vad/"，
    // 会被解析到 CDN 域（cdn.jsdelivr.net/static/vad/...）而 404。绝对 URL 不受基址影响。
    onnxWASMBasePath: location.origin + "/static/vad/",
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
  // 字段级优先级：localStorage 上次值 > .env 默认
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}"); } catch (e) { /* 损坏则忽略 */ }
  for (const k of FLAG_KEYS) {
    if (typeof saved[k] === "boolean") flags[k] = saved[k];
    else if (typeof defaults[k] === "boolean") flags[k] = defaults[k];
  }
  mcpAvailable = defaults.mcp_available !== false;
  // vad_sensitivity：localStorage > .env 默认
  if (typeof saved.vad_sensitivity === "number") flags.vad_sensitivity = saved.vad_sensitivity;
  else if (typeof defaults.vad_sensitivity === "number") flags.vad_sensitivity = defaults.vad_sensitivity;
  renderToggles();
  renderVadSlider();
  applyVadSensitivity();
  sendFlags(); // 连接后立即把会话对齐到用户选择
}

function handleEvent(obj) {
  switch (obj.type) {
    case "tts_format":
      if (obj.sample_rate) ttsSampleRate = obj.sample_rate;
      break;
    case "vision_config":
      if (obj.photo_max_size) photoMaxSize = obj.photo_max_size;
      if (obj.photo_quality) photoQuality = obj.photo_quality;
      break;
    case "config_defaults":
      applyDefaults(obj);
      break;
    case "state":
      setState(obj.state);
      // speaking 期间隐藏 partial（回声会被 STT 短暂转写，避免字幕闪烁）；
      // 切回 listening/thinking 恢复显示
      if (obj.state === "speaking") {
        suppressPartial = true;
        partialEl.textContent = "";
      } else {
        suppressPartial = false;
      }
      break;
    case "partial":
      if (!suppressPartial) partialEl.textContent = obj.text || "";
      break;
    case "user_final":
      partialEl.textContent = "";
      flushTypewriter(); // 收尾上一轮助手气泡
      addBubble("user", obj.text || "");
      break;
    case "delta":
      if (!asstEl) startAssistantBubble();
      asstBuffer += obj.text || "";
      if (!twRAF) twRAF = requestAnimationFrame(twTick);
      break;
    case "tts_start":
      // 播报开始：保留打字机继续走，但确保播放上下文已就绪
      ensurePlayCtx();
      break;
    case "tts_end":
      flushTypewriter();
      break;
    case "cancel_playback":
      stopPlayback();
      flushTypewriter();
      break;
    case "conversation_end":
      flushTypewriter();
      endingByVoice = true;
      if (sources.length === 0) {
        // 已无音频在播，短延迟后收尾（让最后一块 PCM 落地）
        setTimeout(() => { if (endingByVoice) stopSession(); }, 300);
      }
      break;
    case "error":
      addError(obj.message || "发生错误");
      break;
    case "take_photo":
      handleTakePhoto(obj.call_id);
      break;
    case "tool_running":
      if (obj.tool === "take_photo") setHint("正在拍照…");
      break;
  }
}

// ---- 音视频采集（麦克风 + 摄像头）----
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

// ---- 会话 ----
// 把启动阶段的异常翻译成「标题 + 处置建议」，避免裸露的 Permission denied 之类原文
function describeStartError(e) {
  const name = (e && e.name) || "";
  const msg = (e && e.message) || String(e);
  // 麦克风权限被拒（用户阻止 / 地址栏禁止）
  if (name === "NotAllowedError" || /permission/i.test(msg)) {
    return {
      title: "麦克风权限被拒绝",
      hint: "点地址栏左侧锁图标 → 麦克风 → 允许，刷新页面后重试",
    };
  }
  // 非安全上下文 / 浏览器不支持：getUserMedia 仅在 localhost 或 https 可用
  if (name === "SecurityError" || !window.isSecureContext || !navigator.mediaDevices) {
    return {
      title: "无法访问麦克风（来源不安全或浏览器不支持）",
      hint: "请用 Chrome/Edge/Firefox 新版打开 http://127.0.0.1:8000，勿用局域网 IP 或 file://",
    };
  }
  // 无麦克风设备
  if (name === "NotFoundError") {
    return {
      title: "未检测到麦克风",
      hint: "请确认麦克风已连接并在系统声音设置中启用",
    };
  }
  // 设备被其他程序占用
  if (name === "NotReadableError") {
    return {
      title: "麦克风被占用",
      hint: "关闭其他正在使用麦克风的程序（会议/录音等）后重试",
    };
  }
  // WebSocket 未连上 → 后端没起
  if (/连接失败|ws 未创建/i.test(msg)) {
    return {
      title: "无法连接后端服务",
      hint: "请确认后端已启动（默认监听 127.0.0.1:8000）",
    };
  }
  return { title: "启动失败：" + msg, hint: "请检查浏览器麦克风权限与后端服务" };
}

async function startSession() {
  btnStart.disabled = true;
  setHint("");
  endingByVoice = false;  // 新会话开始：清除上次语音结束的遗留标志
  try {
    connect();
    // 等待 WS 打开
    await new Promise((resolve, reject) => {
      if (!ws) return reject(new Error("ws 未创建"));
      if (ws.readyState === WebSocket.OPEN) return resolve();
      const onOpen = () => { ws.removeEventListener("open", onOpen); resolve(); };
      const onErr = () => { ws.removeEventListener("error", onErr); reject(new Error("连接失败")); };
      ws.addEventListener("open", onOpen);
      ws.addEventListener("error", onErr);
    });
    await startAV();
    btnStop.disabled = false;
  } catch (e) {
    const info = describeStartError(e);
    addError(info.title);
    setHint(info.hint);
    btnStart.disabled = false;
    try { if (ws) ws.close(); } catch (er) {}
  }
}

function stopSession() {
  try { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); } catch (e) {}
  try { if (ws) ws.close(); } catch (e) {}
}

btnStart.addEventListener("click", startSession);
btnStop.addEventListener("click", stopSession);

setConn(false);
setState("idle");

// ---- 设置面板交互 ----
function toggleSettings(open) {
  settingsPanel.classList.toggle("hidden", !open);
  if (open) btnCloseSettings.focus();
}
btnSettings.addEventListener("click", (e) => { e.stopPropagation(); toggleSettings(settingsPanel.classList.contains("hidden")); });
btnCloseSettings.addEventListener("click", () => toggleSettings(false));
document.addEventListener("click", (e) => {
  if (!settingsPanel.classList.contains("hidden") && !settingsPanel.contains(e.target) && e.target !== btnSettings) {
    toggleSettings(false);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsPanel.classList.contains("hidden")) toggleSettings(false);
});
toggleRows.forEach((row) => {
  row.querySelector(".switch").addEventListener("click", (e) => {
    e.stopPropagation();
    const sw = e.currentTarget;
    if (sw.disabled) return;
    setFlag(row.dataset.flag, !(sw.getAttribute("aria-checked") === "true"));
  });
  row.querySelector(".switch").addEventListener("keydown", (e) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.click();
    }
  });
});

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

// 初始渲染（未连接时也显示开关，供用户预先设置）
loadFlags();
renderToggles();
renderVadSlider();
