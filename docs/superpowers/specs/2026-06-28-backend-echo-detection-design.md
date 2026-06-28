# 后端文本级回声检测设计

日期：2026-06-28

## 背景与目标

DemoTalk 外放使用时存在**声学回声自循环**：助手 TTS 从扬声器播出的**合成人声**被麦克风收进去，前端 Silero VAD 只能判断「是不是人声」，分不清「是助手在说还是用户在说」，于是照常把回声帧上传给 ASR。ASR 把回声转写成 final 文本后，后端 [session.py `_on_final`](../../../app/session.py) 命中 `if self.state == "speaking"` → 触发 `_barge_in()`：**助手被自己的回声打断**，或播报刚结束就把回声尾巴当成新一轮用户输入，形成「自言自语式」自循环。

浏览器自带的 `echoCancellation: true`（[app.js getUserMedia](../../../static/app.js)）对 VoIP 稳态人声有效，但对 TTS 外放 + 高灵敏度麦克风的回环效果有限。前端 VAD 门控对此完全无效（合成人声被它判为「人声」）。

**目标**：在后端利用「后端知道刚给 TTS 喂了什么文本」这一独有信息，对疑似回声的 STT final 做**文本级**识别并丢弃，消除自循环——**同时完整保留语音 barge-in（助手说话时用户开口打断）**。

**能力边界**：本方案是「文本级」回声检测，不是信号级 AEC。它拦截的是「已被 ASR 转写成 final 的回声」，无法阻止回声在 partial 阶段被短暂转写（由前端优化消除其可见性，见下）。对「ASR 把回声严重错识成与原文完全无关的文字」这种极端情况会漏判——但浏览器 AEC + 前端 VAD 已前置挡掉相当一部分非人声/低能量回声，且严重错识概率低，整体相对现状是显著改善。

## 范围

**包含：**

- 后端 `_on_final` 入口插回声检测闸门：speaking 期间或 speaking 结束后 hangover 窗口内，final 与最近一轮 TTS 参考文本相似度超阈值 → 丢弃
- 回声参考文本来源：复用 `_run_turn` 里每个 `tts.feed(sentence)` 的 sentence，零侵入
- 相似度算法：`difflib.SequenceMatcher`（标准库，无新依赖）+ 文本归一化
- hangover 机制：speaking→其他状态切换时记时间戳，覆盖「扬声器尾音/混响 → ASR 吐出最后一个 final」的延迟
- 三个 `.env` 配置项（总开关 / 相似度阈值 / hangover 时长）
- 前端优化：speaking 期间隐藏 partial 字幕，消除回声 partial 闪烁
- pytest 纯后端单测

**不包含：**

- 不做信号级 AEC（不引入 SpeexDSP / WebRTC AEC3 等 WASM 库，不缓存 TTS PCM 作参考信号）
- 不改动前端麦克风采集链路 / VAD 门控（VAD 行为不变）
- 不改动 STT/LLM/TTS 核心调用（PCM 帧格式、协议事件不变）
- 不新增 HTTP 端点
- 不引入运行时前端开关（回声检测是后端行为，`.env` 配置即可；与 VAD「前端行为下发滑块」范式不同，不下发前端）

## 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 主方向 | **后端文本级回声检测** | 用户选择；保留语音 barge-in（不破坏最自然的交互），利用「后端持有 TTS 文本」独有信息，纯后端无新依赖 |
| 拦截时机 | **`_on_final` 入口、`user_final` 下发前** | final 是断句完成的稳定文本，比对最可靠；partial 太碎不可靠，且由前端优化消除可见性 |
| 参考文本来源 | **复用 `tts.feed(sentence)` 的 sentence** | 零侵入——后端本就持有这些文本，无需额外采集/缓存 PCM |
| 相似度算法 | **`difflib.SequenceMatcher.ratio()` + 文本归一化** | 标准库，无新依赖；ratio 基于最长连续匹配，对回声的错字/漏字/多字有容错；对每句 + 整体拼接取最大，覆盖「回声把多句连成一段转写」 |
| 触发窗口 | **speaking 期间 + speaking 结束后 hangover** | speaking 期间是回声主来源；hangover 覆盖「说完→尾音→最后一个 final」延迟 |
| hangover 默认 | **1200ms** | 覆盖 `MAX_SENTENCE_SILENCE=800ms`（ASR 句尾静音断句）+ 扬声器尾音/房间混响余量 |
| 相似度阈值默认 | **0.6** | 平衡：太低会误吞用户真话，太高会漏判回声；0.6 对「中文短句、归一化后」经验上能抓住绝大多数回声且极少误杀 |
| partial 闪烁 | **前端 speaking 期间隐藏 partial** | 用户选择；消除回声 partial 在字幕区闪烁；代价（打断前奏 partial 也不显示）可接受，因打断反馈是「助手停下」 |

## 设计

### 1. 核心机制：`_on_final` 回声闸门

在 [session.py `_on_final`](../../../app/session.py) 现有的 strip / empty 检查**之后**、`user_final` 下发**之前**，插入回声检测：

```python
async def _on_final(self, text: str) -> None:
    if not self._running:
        return
    if self._ending:
        return
    text = text.strip()
    if not text:
        return

    # ---- 回声检测：speaking 期间或 hangover 内，疑似回声则丢弃 ----
    if self._is_echo(text):
        log.info("回声检测：丢弃疑似回声输入「%s」", text)
        return  # 不发 user_final、不 barge-in、不开新一轮

    await self._send({"type": "user_final", "text": text})

    if self.state == "speaking":
        if self.barge_in_enabled:
            await self._barge_in()
        else:
            return

    self._current_turn += 1
    turn = self._current_turn
    self._turn_task = asyncio.create_task(self._run_turn(text, turn))
```

**为什么这样既治自循环又保 barge-in：**

- 回声 = 助手刚说的话 → 与 TTS 参考高度相似 → 命中 `_is_echo` → return，**不触发 barge-in、不开新轮** → 自循环消失
- 用户真开口 = 内容与助手不同 → 不命中 → 走原逻辑 → speaking 期间正常 barge-in → 语音打断完整保留

### 2. `_is_echo` 判定逻辑

```python
def _is_echo(self, text: str) -> bool:
    if not config.ENABLE_ECHO_DETECT:
        return False
    # 只在「可能产生回声」的窗口内判：speaking 或 speaking 结束后 hangover 内
    if self.state != "speaking" and not self._in_echo_hangover():
        return False
    if not self._echo_ref:           # 无最近播报内容可比对
        return False
    norm = _normalize(text)
    if not norm:
        return False
    # 对每句参考 + 整体拼接取最大相似度
    refs = self._echo_ref + ["".join(self._echo_ref)]
    ref_norms = [_normalize(r) for r in refs if _normalize(r)]
    return any(_similarity(norm, rn) >= config.ECHO_SIMILARITY_THRESHOLD for rn in ref_norms)
```

**为何要比「整体拼接」**：回声可能把助手说的多句话连成一整段转写（「你好。我是助手。」→ 回声转成「你好我是助手」）。逐句比对相似度都低（每句只占一部分）会漏判；拼接后整体比对能抓住这种情形。`_echo_ref` 只保留最近一轮（见下），拼接串不会无限长（DemoTalk LLM prompt 要求 1-2 句简短回答）。

### 3. 回声参考文本来源（零侵入）

[session.py `_run_turn`](../../../app/session.py) 里每个分句 `sentence = buffer[: m.end()]` 后 `tts.feed(sentence)`。在该处旁路 append 到 `self._echo_ref`：

```python
# _run_turn 开头（thinking 之前）：清空上一轮参考
self._echo_ref = []
...
# 每个 tts.feed(sentence) 处：
tts.feed(sentence)
self._echo_ref.append(sentence)
fed_any = True
...
# 末尾 tts.feed(buffer) 处：
tts.feed(buffer)
if buffer.strip():
    self._echo_ref.append(buffer)
```

**生命周期**：`_echo_ref` 在每轮 `_run_turn` 开头清空、随喂 TTS 重建。barge-in / hangover 窗口内新轮尚未开始，`_echo_ref` 仍是**上一轮**内容——正好比对「刚播完那轮」的回声与回声尾巴。

### 4. 相似度算法（标准库）

```python
import difflib
import re

# 归一化：去标点/空格/符号，转小写；保留中文与字母数字
_STRIP = re.compile(r"\W+", re.UNICODE)  # Python3 默认 re.UNICODE：\W = 非字母数字下划线（含中文标点、空格）

def _normalize(s: str) -> str:
    return _STRIP.sub("", s).lower()

def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
```

- `difflib` 是标准库，**无新依赖**
- `SequenceMatcher.ratio()` 基于最长连续匹配子串计算，对回声的错字/漏字/多字/标点丢失天然容错
- 归一化去掉标点空格后比对，避免「你好。」vs「你好」因标点判低

### 5. hangover 窗口

[session.py `_set_state`](../../../app/session.py) 检测 speaking→非 speaking 时记录时间戳：

```python
async def _set_state(self, state: str) -> None:
    if self.state == "speaking" and state != "speaking":
        self._speaking_ended_at = time.monotonic()
    self.state = state
    await self._send({"type": "state", "state": state})
```

```python
def _in_echo_hangover(self) -> bool:
    if self._speaking_ended_at == 0.0:
        return False
    return (time.monotonic() - self._speaking_ended_at) * 1000 < config.ECHO_HANGOVER_MS
```

用 `time.monotonic()`（不受系统时钟回拨影响，适合间隔测量）。

**Session.__init__ 新增字段：**

```python
self._echo_ref: list[str] = []        # 最近一轮喂给 TTS 的句子（回声比对参考）
self._speaking_ended_at: float = 0.0  # speaking→其他 的时刻（monotonic），用于 hangover
```

### 6. 与 barge-in 的交互

- 回声检测在 barge-in 判断**之前**，且**独立于 `barge_in_enabled`**
- speaking 期间：回声 → 丢弃；真用户声 → 通过 → 若 `barge_in_enabled` 则 barge-in
- `barge_in_enabled=False` 时：speaking 期间所有 final 本就被忽略（现行 return）；但 speaking 结束后 hangover 内的回声尾巴处于 listening 状态，不受 barge_in 开关控制——回声检测仍独立生效，挡住「说完后回声尾巴被当新输入」

### 7. 前端优化：speaking 期间隐藏 partial

[app.js `handleEvent`](../../../static/app.js) 的 `state` 与 `partial` 分支配合：

- 收到 `state == "speaking"`：清空 `partialEl.textContent`，置一个模块标志 `suppressPartial = true`
- 收到 `partial` 事件：仅当 `!suppressPartial` 时更新 `partialEl`；否则忽略
- 收到 `state` 切回 `listening` / `thinking`：`suppressPartial = false`（恢复显示）

效果：助手播报期间，回声的 partial 转写不会闪现在字幕区。代价：speaking 期间用户开口的 partial（barge-in 前奏）也不显示——但 barge-in 的可见反馈是「助手立即停下」，partial 不显示可接受。

### 8. 配置项（`app/config.py`）

```python
# ---- 回声检测（外放自循环防护；后端文本级，比对 STT final 与最近 TTS 文本）----
ENABLE_ECHO_DETECT: bool = _bool("ENABLE_ECHO_DETECT", True)
ECHO_SIMILARITY_THRESHOLD: float = _float("ECHO_SIMILARITY_THRESHOLD", 0.6)
ECHO_HANGOVER_MS: int = _int("ECHO_HANGOVER_MS", 1200)
```

`.env.example` 同步添加三项及注释。**不下发前端**（与 VAD 的 `config_defaults` 范式不同——VAD 是前端行为才下发滑块；回声检测是纯后端行为）。

## 边缘情况

1. **回声转写与原文高度相似** → 命中阈值，丢弃 ✓
2. **回声把多句连成一段转写** → 整体拼接比对命中 ✓
3. **回声有错字/漏字/标点丢失** → 归一化 + difflib 容错 ✓
4. **用户故意复述助手原话** → 会被误判为回声吞掉（罕见；可调低 `ECHO_SIMILARITY_THRESHOLD` 缓解）
5. **回声被 ASR 严重错识（与原文无关）** → 漏判，按正常 final 处理（浏览器 AEC + VAD 已前置挡掉相当部分；严重错识概率低）
6. **speaking 刚结束的回声尾巴** → hangover 窗口内 + `_echo_ref` 仍是上轮 → 命中丢弃 ✓
7. **`_echo_ref` 为空**（首轮、或本轮尚未喂 TTS）→ `_is_echo` 直接返回 False，放行 ✓
8. **barge-in 期间** → 真用户声通过检测 → barge-in → 新轮清空重建 `_echo_ref`，与现有逻辑无缝衔接 ✓
9. **`ENABLE_ECHO_DETECT=False`** → `_is_echo` 直接返回 False，完全回退现状 ✓
10. **partial 闪烁** → 前端 speaking 期间隐藏 partial 消除 ✓
11. **`time.monotonic()` 跨度** → monotonic 单调递增，无时钟回拨问题 ✓

## 文件清单

| 文件 | 改动 |
|---|---|
| `app/session.py` | `_on_final` 插回声闸门；`_is_echo` / `_in_echo_hangover` / `_normalize` / `_similarity`；`_set_state` 记 hangover 时间戳；`_run_turn` 维护 `_echo_ref`；`__init__` 新增字段 |
| `app/config.py` | 新增 `ENABLE_ECHO_DETECT` / `ECHO_SIMILARITY_THRESHOLD` / `ECHO_HANGOVER_MS` |
| `.env.example` | 同步三项配置及注释 |
| `static/app.js` | speaking 期间隐藏 partial（`suppressPartial` 标志） |
| `tests/test_echo_detect.py` | 新增：归一化/相似度/`_is_echo` 各情形/`_on_final` 丢弃与放行/`_echo_ref` 生命周期/hangover |
| `tests/test_config.py`（扩展，若已存在） | 断言三个新配置项读取（默认值 + 环境变量覆盖） |
| `README.md` | 配置表补充三项 + 「回声检测」小节说明机制与能力边界 |

## 验收标准

1. **自循环消除**：外放下，助手播报时与播报后，麦克风收到的回声不再被当成用户输入——助手不会对着自己的回声「自言自语」，也不会播报刚结束就触发新一轮
2. **语音 barge-in 保留**：助手说话时用户开口说**不同内容**，仍能正常打断（回声检测放行真用户声）
3. **安静对话不劣化**：无回声场景下，正常说话、正常识别、正常回应，体验不变（首轮 `_echo_ref` 为空时放行）
4. **partial 不闪烁**：助手播报期间字幕区不出现回声 partial 转写
5. **可配置**：`ENABLE_ECHO_DETECT=false` 完全回退现状；调 `ECHO_SIMILARITY_THRESHOLD` / `ECHO_HANGOVER_MS` 行为可观察变化
6. **hangover 覆盖尾巴**：助手说完后 1.2s 内的回声 final 被丢弃
7. **全部 pytest 通过**

## 已知局限（需向用户说明）

- **文本级，非信号级**：拦截 final 级回声；partial 级回声由前端隐藏消除可见性，但回声仍会被 ASR 转写一道（消耗少量 ASR 调用）。
- **依赖转写相似度**：ASR 把回声严重错识成无关文字时会漏判（概率低，且有 AEC + VAD 前置）。
- **复述误杀**：用户故意复述助手原话会被当回声吞掉（罕见，可调阈值）。
- **根治需要物理手段**：彻底消除回声的最优解是**戴耳机**（扬声器声音物理上进不了麦克风）。本方案是外放场景下的软件缓解，文档应同时给出耳机建议。
