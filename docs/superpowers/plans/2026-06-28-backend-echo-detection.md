# 后端文本级回声检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在后端 `_on_final` 下发前加一道回声检测闸门——speaking 期间或说完后 hangover 窗口内，若 STT final 与最近一轮 TTS 文本相似度超阈值则判为回声丢弃，消除外放自循环，同时完整保留语音 barge-in。

**Architecture:** 纯后端文本级方案。复用 `_run_turn` 里每个 `tts.feed(sentence)` 的 sentence 作为回声参考（零侵入），用标准库 `difflib.SequenceMatcher` 算相似度；`_set_state` 在 speaking→其他时记 `time.monotonic()` 实现 hangover；前端加一个小优化在 speaking 期间隐藏 partial 字幕。

**Tech Stack:** Python（FastAPI 既有后端）、`difflib` + `re`（标准库，无新依赖）、pytest + pytest-asyncio（既有测试）、原生 JS（前端 partial 隐藏）。

**Spec:** `docs/superpowers/specs/2026-06-28-backend-echo-detection-design.md`

**命名约定（实现时统一使用，与 spec 里的 `_normalize`/`_similarity` 对应）：**
- 模块级私有函数：`_echo_normalize(s)`、`_echo_similarity(a, b)`
- Session 实例字段：`self._echo_ref: list[str]`、`self._speaking_ended_at: float`
- Session 方法：`self._is_echo(text) -> bool`、`self._in_echo_hangover() -> bool`

---

## File Structure

| 文件 | 责任 | 改动类型 |
|---|---|---|
| `app/config.py` | 三个新配置项：总开关 / 相似度阈值 / hangover 时长 | 追加 |
| `app/session.py` | 回声检测核心：模块函数 + Session 字段 + hangover + `_echo_ref` 维护 + `_is_echo` + `_on_final` 闸门 | 修改 |
| `.env.example` | 同步三个配置项及注释 | 追加 |
| `static/app.js` | speaking 期间隐藏 partial 字幕 | 修改 |
| `tests/test_echo_detect.py` | 回声检测纯后端单测（归一化/相似度/hangover/`_echo_ref`/`_is_echo`/`_on_final`） | 新建 |
| `tests/test_config.py` | 扩展：三个新配置项默认值 | 追加 |
| `README.md` | 配置表三项 + 「回声检测」小节 | 修改 |

任务依赖链：Task 1（配置）→ Task 2（模块函数）→ Task 3（hangover 基础设施）→ Task 4（`_echo_ref` 维护）→ Task 5（`_is_echo`）→ Task 6（`_on_final` 闸门）→ Task 7（前端 partial）→ Task 8（文档）。Task 1-6 是后端核心，有顺序依赖；Task 7 前端、Task 8 文档可独立。

---

### Task 1: 配置项

**Files:**
- Modify: `app/config.py`（在 `VAD_SENSITIVITY` 行之后）
- Modify: `.env.example`（在 `VAD_SENSITIVITY=50` 行之后）
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
def test_echo_detect_defaults():
    assert config.ENABLE_ECHO_DETECT is True
    assert config.ECHO_SIMILARITY_THRESHOLD == 0.6
    assert config.ECHO_HANGOVER_MS == 1200
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_config.py::test_echo_detect_defaults -v`
Expected: FAIL — `AttributeError: module 'app.config' has no attribute 'ENABLE_ECHO_DETECT'`

- [ ] **Step 3: 实现配置项**

在 `app/config.py` 的 `VAD_SENSITIVITY` 那一行之后（`# ---- 服务 ----` 之前）追加：

```python
# ---- 回声检测（外放自循环防护；后端文本级，比对 STT final 与最近 TTS 文本）----
ENABLE_ECHO_DETECT: bool = _bool("ENABLE_ECHO_DETECT", True)
ECHO_SIMILARITY_THRESHOLD: float = _float("ECHO_SIMILARITY_THRESHOLD", 0.6)
ECHO_HANGOVER_MS: int = _int("ECHO_HANGOVER_MS", 1200)
```

在 `.env.example` 的 `VAD_SENSITIVITY=50` 行之后追加（注释 + 三行）：

```
# 回声检测（外放自循环防护）：speaking 期间/结束后 hangover 内，STT final 与最近 TTS
# 文本相似度超阈值则判为回声丢弃。ENABLE_ECHO_DETECT=false 关闭；阈值越低越激进判回声。
ENABLE_ECHO_DETECT=true
ECHO_SIMILARITY_THRESHOLD=0.6
ECHO_HANGOVER_MS=1200
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（含新增 `test_echo_detect_defaults` 与既有全部）

- [ ] **Step 5: 提交**

```bash
git add app/config.py .env.example tests/test_config.py
git commit -m "feat(config): 回声检测三项配置项"
```

---

### Task 2: 文本归一化 + 相似度模块函数

**Files:**
- Modify: `app/session.py`（顶部 import + 模块级函数）
- Test: `tests/test_echo_detect.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_echo_detect.py`：

```python
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import config
from app.session import Session, _echo_normalize, _echo_similarity


def test_echo_normalize_strips_punctuation_spaces_case():
    assert _echo_normalize("你好。World！") == "你好world"
    assert _echo_normalize("  Hello, World!  ") == "helloworld"
    assert _echo_normalize("你好，我是助手。") == "你好我是助手"
    assert _echo_normalize("") == ""
    assert _echo_normalize("。！？, ") == ""


def test_echo_similarity_identical_is_one():
    assert _echo_similarity("你好世界", "你好世界") == 1.0


def test_echo_similarity_disjoint_is_zero():
    assert _echo_similarity("abc", "xyz") == 0.0


def test_echo_similarity_high_overlap():
    # 回声带轻微错字/漏字，仍应高于阈值
    sim = _echo_similarity("你好我是语音助手", "你好，我是语音助手。")
    assert sim >= 0.6


def test_echo_similarity_empty_returns_zero():
    assert _echo_similarity("", "abc") == 0.0
    assert _echo_similarity("abc", "") == 0.0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: FAIL — `ImportError: cannot import name '_echo_normalize' from 'app.session'`

- [ ] **Step 3: 实现模块函数**

在 `app/session.py` 顶部 import 区，把：

```python
import asyncio
import json
import logging
import re
```

改为（加 `difflib`；`time` 留到 Task 3 加，本任务只需 difflib）：

```python
import asyncio
import difflib
import json
import logging
import re
```

在模块级常量 `_SENTENCE_END = re.compile(r"[。！？!?；;\n]")` 之后追加：

```python
# 回声检测：文本归一化（去标点/空格/符号，转小写；保留中文与字母数字）
_ECHO_STRIP = re.compile(r"\W+", re.UNICODE)


def _echo_normalize(s: str) -> str:
    """归一化用于回声比对：去掉标点/空格/符号，转小写。Python3 默认 re.UNICODE 下
    \W = 非字母数字下划线（含中英文标点、空格），中文字符属于 \w 故保留。"""
    return _ECHO_STRIP.sub("", s).lower()


def _echo_similarity(a: str, b: str) -> float:
    """两个已归一化字符串的相似度（difflib 最长连续匹配比例，0-1）。"""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: PASS（5 个新增测试全过）

- [ ] **Step 5: 提交**

```bash
git add app/session.py tests/test_echo_detect.py
git commit -m "feat(session): 回声比对文本归一化与相似度模块函数"
```

---

### Task 3: Session hangover 基础设施

**Files:**
- Modify: `app/session.py`（import time + `__init__` 字段 + `_set_state` + 新方法 `_in_echo_hangover`）
- Test: `tests/test_echo_detect.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_echo_detect.py` 末尾追加（`_make_session` 复用 `tests/test_session_end.py` 范式，带 `_running=True` 以便后续 `_on_final` 测试）：

```python
def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    loop = asyncio.get_running_loop()
    s = Session(ws, loop)
    s._running = True
    return s, ws


async def test_in_echo_hangover_false_initially():
    s, _ = _make_session()
    assert s._in_echo_hangover() is False
    assert s._speaking_ended_at == 0.0


async def test_in_echo_hangover_true_within_window():
    s, _ = _make_session()
    s._speaking_ended_at = time.monotonic()  # 刚结束 speaking
    assert s._in_echo_hangover() is True


async def test_in_echo_hangover_false_after_window():
    s, _ = _make_session()
    # 超过 hangover 窗口
    s._speaking_ended_at = time.monotonic() - (config.ECHO_HANGOVER_MS / 1000) - 0.1
    assert s._in_echo_hangover() is False


async def test_set_state_records_speaking_end_timestamp():
    s, _ = _make_session()
    assert s._speaking_ended_at == 0.0
    await s._set_state("speaking")
    assert s._speaking_ended_at == 0.0  # 进入 speaking 不记录
    await s._set_state("listening")
    assert s._speaking_ended_at != 0.0  # 离开 speaking 才记录
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: FAIL — `AttributeError: 'Session' object has no attribute '_in_echo_hangover'`

- [ ] **Step 3: 实现 hangover 基础设施**

3a. 在 `app/session.py` 顶部 import 区加 `time`（Task 2 已加 `difflib`，这里加在它旁边）：

```python
import asyncio
import difflib
import json
import logging
import re
import time
```

3b. 在 `Session.__init__` 里，紧接 `self._end_fallback: asyncio.Task | None = None` 之后追加两个字段：

```python
        # 回声检测：最近一轮喂给 TTS 的句子（比对参考）；speaking 结束时刻（hangover 窗口）
        self._echo_ref: list[str] = []
        self._speaking_ended_at: float = 0.0
```

3c. 把 `_set_state` 方法：

```python
    async def _set_state(self, state: str) -> None:
        self.state = state
        await self._send({"type": "state", "state": state})
```

改为：

```python
    async def _set_state(self, state: str) -> None:
        if self.state == "speaking" and state != "speaking":
            # 离开 speaking：记录时刻，供回声检测 hangover 窗口使用
            self._speaking_ended_at = time.monotonic()
        self.state = state
        await self._send({"type": "state", "state": state})
```

3d. 在 `_set_state` 方法之后追加新方法 `_in_echo_hangover`（放在 `# ---------- 辅助 ----------` 区内即可，紧邻 `_set_state` 之后）：

```python
    def _in_echo_hangover(self) -> bool:
        """speaking 结束后是否仍处于回声 hangover 窗口内（覆盖扬声器尾音/混响产生的回声 final）。"""
        if self._speaking_ended_at == 0.0:
            return False
        return (time.monotonic() - self._speaking_ended_at) * 1000 < config.ECHO_HANGOVER_MS
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: PASS（Task 2 的 5 个 + 本任务 4 个，共 9 个）

- [ ] **Step 5: 提交**

```bash
git add app/session.py tests/test_echo_detect.py
git commit -m "feat(session): 回声检测 hangover 基础设施（字段+时间戳+窗口判定）"
```

---

### Task 4: `_echo_ref` 参考文本维护

**Files:**
- Modify: `app/session.py`（`_run_turn` 三处）
- Test: `tests/test_echo_detect.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_echo_detect.py` 末尾追加（复用 `tests/test_session_end.py` 的 `_stub_tts` + `fake_astream_once` 范式）：

```python
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


async def test_run_turn_clears_and_populates_echo_ref(monkeypatch):
    """_run_turn 开头清空上轮残留，并把喂给 TTS 的每个句子累积进 _echo_ref。"""
    _stub_tts(monkeypatch)
    s, _ = _make_session()
    s._current_turn = 1
    s._echo_ref = ["残留上轮内容"]  # 预置上轮残留，验证会被清空

    async def fake_astream_once(tools=None):
        s.llm._history.append({"role": "assistant", "content": "你好。我是助手。"})
        yield {"type": "text", "text": "你好。我是助手。"}
        yield {"type": "done", "tool_calls": [], "finish_reason": "stop"}

    s.llm.astream_once = fake_astream_once
    s.llm.add_user = lambda t: None
    s.llm.add_tool = lambda cid, c: None

    await s._run_turn("你好", turn=1)

    assert "残留上轮内容" not in s._echo_ref  # 开头已清空
    assert s._echo_ref == ["你好。", "我是助手。"]  # 按句号切分的两句


async def test_run_turn_echo_ref_includes_trailing_buffer(monkeypatch):
    """末尾无句号的 buffer 残片也应进 _echo_ref。"""
    _stub_tts(monkeypatch)
    s, _ = _make_session()
    s._current_turn = 1

    async def fake_astream_once(tools=None):
        s.llm._history.append({"role": "assistant", "content": "完整一句。还有残片"})
        yield {"type": "text", "text": "完整一句。还有残片"}
        yield {"type": "done", "tool_calls": [], "finish_reason": "stop"}

    s.llm.astream_once = fake_astream_once
    s.llm.add_user = lambda t: None
    s.llm.add_tool = lambda cid, c: None

    await s._run_turn("继续", turn=1)

    assert "完整一句。" in s._echo_ref
    assert "还有残片" in s._echo_ref
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_echo_detect.py::test_run_turn_clears_and_populates_echo_ref tests/test_echo_detect.py::test_run_turn_echo_ref_includes_trailing_buffer -v`
Expected: FAIL — `_echo_ref` 仍是预置值/空（`AssertionError`，因为 `_run_turn` 尚未维护 `_echo_ref`）

- [ ] **Step 3: 实现 `_echo_ref` 维护**

在 `app/session.py` 的 `_run_turn` 里做三处修改。

3a. 在 `await self._set_state("thinking")` 之后加一行清空。原：

```python
        await self._set_state("thinking")
        tts = TTSService(
```

改为：

```python
        await self._set_state("thinking")
        self._echo_ref = []  # 本轮重建回声参考
        tts = TTSService(
```

3b. 在按句切分喂 TTS 处加 append。原：

```python
                            sentence = buffer[: m.end()]
                            buffer = buffer[m.end():]
                            if active():
                                tts.feed(sentence)
                                fed_any = True
```

改为：

```python
                            sentence = buffer[: m.end()]
                            buffer = buffer[m.end():]
                            if active():
                                tts.feed(sentence)
                                self._echo_ref.append(sentence)
                                fed_any = True
```

3c. 在末尾 buffer 残片喂 TTS 处加 append。原：

```python
                if buffer.strip() and active():
                    tts.feed(buffer)
                    fed_any = True
```

改为：

```python
                if buffer.strip() and active():
                    tts.feed(buffer)
                    self._echo_ref.append(buffer)
                    fed_any = True
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: PASS（前 9 个 + 本任务 2 个，共 11 个）

- [ ] **Step 5: 提交**

```bash
git add app/session.py tests/test_echo_detect.py
git commit -m "feat(session): _run_turn 维护回声参考文本 _echo_ref"
```

---

### Task 5: `_is_echo` 判定

**Files:**
- Modify: `app/session.py`（新增 `_is_echo` 方法，紧邻 `_in_echo_hangover` 之后）
- Test: `tests/test_echo_detect.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_echo_detect.py` 末尾追加：

```python
# ---- _is_echo 各情形 ----

async def test_is_echo_true_when_similar_during_speaking():
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好，我是语音助手。"]
    assert s._is_echo("你好我是语音助手") is True  # 高度相似


async def test_is_echo_false_when_listening_no_hangover():
    s, _ = _make_session()
    s.state = "listening"
    s._speaking_ended_at = 0.0  # 不在 hangover
    s._echo_ref = ["你好，我是语音助手。"]
    assert s._is_echo("你好我是语音助手") is False


async def test_is_echo_true_in_hangover():
    s, _ = _make_session()
    s.state = "listening"
    s._speaking_ended_at = time.monotonic()  # hangover 内
    s._echo_ref = ["你好，我是语音助手。"]
    assert s._is_echo("你好我是语音助手") is True


async def test_is_echo_false_when_ref_empty():
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = []  # 首轮无参考
    assert s._is_echo("随便说点什么") is False


async def test_is_echo_false_when_user_says_different():
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["今天天气真好。"]
    assert s._is_echo("帮我订个闹钟") is False  # 内容不同


async def test_is_echo_concatenated_multi_sentence():
    """回声把多句连成一整段转写时，靠整体拼接比对命中。"""
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好。", "我是助手。"]
    assert s._is_echo("你好我是助手") is True


async def test_is_echo_respects_disable_switch(monkeypatch):
    monkeypatch.setattr("app.config.ENABLE_ECHO_DETECT", False)
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好。"]
    assert s._is_echo("你好") is False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: FAIL — `AttributeError: 'Session' object has no attribute '_is_echo'`

- [ ] **Step 3: 实现 `_is_echo`**

在 `app/session.py` 的 `_in_echo_hangover` 方法之后追加：

```python
    def _is_echo(self, text: str) -> bool:
        """判断 STT final 是否为助手播报的回声：仅在 speaking 或 hangover 窗口内，
        且与最近一轮 TTS 参考文本（逐句 + 整体拼接）相似度超阈值时为 True。"""
        if not config.ENABLE_ECHO_DETECT:
            return False
        if self.state != "speaking" and not self._in_echo_hangover():
            return False
        if not self._echo_ref:
            return False
        norm = _echo_normalize(text)
        if not norm:
            return False
        # 逐句 + 整体拼接都比对，覆盖「回声把多句连成一段转写」
        refs = list(self._echo_ref) + ["".join(self._echo_ref)]
        for r in refs:
            rn = _echo_normalize(r)
            if rn and _echo_similarity(norm, rn) >= config.ECHO_SIMILARITY_THRESHOLD:
                return True
        return False
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: PASS（前 11 个 + 本任务 7 个，共 18 个）

- [ ] **Step 5: 提交**

```bash
git add app/session.py tests/test_echo_detect.py
git commit -m "feat(session): _is_echo 回声判定（窗口+相似度+拼接比对）"
```

---

### Task 6: `_on_final` 回声闸门集成

**Files:**
- Modify: `app/session.py`（`_on_final` 插入闸门）
- Test: `tests/test_echo_detect.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_echo_detect.py` 末尾追加：

```python
# ---- _on_final 回声闸门集成 ----

async def test_on_final_drops_echo_no_barge_no_turn():
    """speaking 期间收到回声 final：丢弃，不 barge-in、不开新轮、不发 user_final。"""
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好，我是语音助手。"]
    s.barge_in_enabled = True

    barge = []

    async def fake_barge():
        barge.append(1)

    s._barge_in = fake_barge
    s._run_turn = AsyncMock()

    sent = []

    async def cap_send(obj):
        sent.append(obj["type"])

    s._send = cap_send

    await s._on_final("你好我是语音助手")  # 回声

    assert barge == []  # 未 barge-in
    s._run_turn.assert_not_called()  # 未开新轮
    assert "user_final" not in sent  # 未下发


async def test_on_final_passes_real_voice_barges_in():
    """speaking 期间收到真用户声（内容不同）：通过回声检测，正常 barge-in 并开新轮。"""
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好，我是语音助手。"]
    s.barge_in_enabled = True

    barge = []

    async def fake_barge():
        barge.append(1)

    s._barge_in = fake_barge
    s._run_turn = AsyncMock()
    try:
        await s._on_final("帮我订个闹钟")  # 真用户声
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()

    assert barge == [1]  # 正常 barge-in
    s._run_turn.assert_called_once()  # barge-in 后开新轮


async def test_on_final_drops_echo_tail_in_hangover():
    """listening 但 hangover 内的回声尾巴：丢弃，不开新轮。"""
    s, _ = _make_session()
    s.state = "listening"
    s._speaking_ended_at = time.monotonic()  # hangover 内
    s._echo_ref = ["再见，下次再聊。"]

    s._run_turn = AsyncMock()
    sent = []

    async def cap_send(obj):
        sent.append(obj["type"])

    s._send = cap_send

    await s._on_final("再见下次再聊")  # 回声尾巴

    s._run_turn.assert_not_called()
    assert "user_final" not in sent


async def test_on_final_passes_when_first_turn_empty_ref():
    """首轮（_echo_ref 为空）：正常用户输入不被误判，正常开新轮。"""
    s, _ = _make_session()
    s.state = "listening"
    s._echo_ref = []  # 首轮无参考
    s._run_turn = AsyncMock()
    try:
        await s._on_final("你好")
    finally:
        if s._turn_task and not s._turn_task.done():
            s._turn_task.cancel()

    s._run_turn.assert_called_once()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_echo_detect.py::test_on_final_drops_echo_no_barge_no_turn -v`
Expected: FAIL — 回声未被丢弃，`barge == [1]` 或 `_run_turn` 被调（闸门尚未插入）

- [ ] **Step 3: 实现闸门**

在 `app/session.py` 的 `_on_final` 里，把：

```python
        text = text.strip()
        if not text:
            return
        await self._send({"type": "user_final", "text": text})
```

改为（在 `user_final` 下发前插入回声检测）：

```python
        text = text.strip()
        if not text:
            return

        # 回声检测：speaking 期间或 hangover 内，疑似助手播报的回声则丢弃，
        # 不发 user_final、不 barge-in、不开新一轮（真用户声内容不同会通过）
        if self._is_echo(text):
            log.info("回声检测：丢弃疑似回声输入「%s」", text)
            return

        await self._send({"type": "user_final", "text": text})
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_echo_detect.py -v`
Expected: PASS（前 18 个 + 本任务 4 个，共 22 个）

再跑全套确认无回归：

Run: `uv run pytest -q`
Expected: 全部 PASS（含既有 test_session_flags / test_session_end / test_session_photo 等）

- [ ] **Step 5: 提交**

```bash
git add app/session.py tests/test_echo_detect.py
git commit -m "feat(session): _on_final 回声闸门——丢弃疑似回声，保留 barge-in"
```

---

### Task 7: 前端 speaking 期间隐藏 partial

**Files:**
- Modify: `static/app.js`（`handleEvent` 的 `state` 与 `partial` 分支 + 模块标志）
- 验证：手动（项目无 JS 测试框架，沿用 VAD feature 的手动验证惯例）

- [ ] **Step 1: 加模块标志**

在 `static/app.js` 顶部模块级变量区（`let vadSending = false;` 附近）追加：

```javascript
let suppressPartial = false;   // speaking 期间隐藏 partial，避免回声转写在字幕区闪烁
```

- [ ] **Step 2: 改 `handleEvent` 的 `state` 分支**

在 `handleEvent` 的 `case "state":` 分支，原：

```javascript
    case "state":
      setState(obj.state);
      break;
```

改为：

```javascript
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
```

- [ ] **Step 3: 改 `handleEvent` 的 `partial` 分支**

原：

```javascript
    case "partial":
      partialEl.textContent = obj.text || "";
      break;
```

改为：

```javascript
    case "partial":
      if (!suppressPartial) partialEl.textContent = obj.text || "";
      break;
```

- [ ] **Step 4: 手动验证**

启动服务：`uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，浏览器打开 `http://127.0.0.1:8000`，点「开始对话」。

验证点：
1. **partial 不闪烁**：助手播报期间，字幕区（`#partial`）不再出现回声转写文字（保持空）。
2. **自循环消除**：外放下，助手说完后不会「把自己的话当新输入」再触发一轮回应。
3. **语音 barge-in 保留**：助手说话时开口说**不同内容**（如「停一下」「换个话题」），助手仍会停下并回应新内容。
4. **正常对话不劣化**：安静说话，识别与回应正常；首轮助手还没说话时，用户 partial 字幕正常显示。
5. **可关闭**：`.env` 设 `ENABLE_ECHO_DETECT=false` 重启，回退原行为（验证开关有效）。

- [ ] **Step 5: 提交**

```bash
git add static/app.js
git commit -m "feat(web): speaking 期间隐藏 partial，消除回声字幕闪烁"
```

---

### Task 8: README 文档

**Files:**
- Modify: `README.md`（配置表 + 「回声检测」小节）

- [ ] **Step 1: 配置表追加三行**

在 `README.md` 配置项表格里，`VAD_SENSITIVITY` 行之后追加：

```markdown
| `ENABLE_ECHO_DETECT` | `true` | 回声检测总开关（外放自循环防护） |
| `ECHO_SIMILARITY_THRESHOLD` | `0.6` | 回声相似度阈值 0-1，越低越激进判回声 |
| `ECHO_HANGOVER_MS` | `1200` | speaking 结束后仍检测的窗口(毫秒) |
```

- [ ] **Step 2: 追加「回声检测」小节**

在 `### 语音门控（VAD）` 小节之后、`### 关于 TTS 模型（重要）` 之前，插入：

```markdown
### 回声检测（外放自循环防护）

外放使用时，助手 TTS 的合成人声会被麦克风收回去（浏览器自带 AEC 对 TTS 外放效果有限），前端 VAD 又分不清「是助手在说还是用户在说」，导致回声被转写、助手对着自己的回声「自言自语」自循环。

后端做**文本级回声检测**：`_on_final` 下发前，若处于 speaking 期间或说完后 1.2s 内，且 STT 转写与最近一轮 TTS 文本相似度 ≥ 阈值，判为回声丢弃——不发用户消息、不触发 barge-in、不开新一轮。真用户说的话内容不同，正常通过，**语音 barge-in 完整保留**。前端另在 speaking 期间隐藏 partial 字幕，消除回声转写的闪烁。

- `ENABLE_ECHO_DETECT=false` 可关闭；`ECHO_SIMILARITY_THRESHOLD` 调相似度松紧；`ECHO_HANGOVER_MS` 调说完后的检测窗口。
- **能力边界**：文本级方案，对 ASR 把回声严重错识成无关文字的极端情况会漏判（概率低，且有浏览器 AEC + 前端 VAD 前置挡一部分）。**根治回声的最优解是戴耳机**（扬声器声音物理上进不了麦克风）。
```

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: 回声检测配置项与说明"
```

---

## 完成标准

- 全部 8 个任务的 checkbox 勾完，每个任务有独立提交。
- `uv run pytest -q` 全绿（含新增 `tests/test_echo_detect.py` 的 22 个 + 既有全部）。
- 手动验证 5 个验证点全部通过（Task 7 Step 4）。
- README / `.env.example` / `config.py` 三处配置一致。
