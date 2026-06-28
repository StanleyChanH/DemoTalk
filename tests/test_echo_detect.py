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
    """回声把多句连成一整段转写时，靠整体拼接比对命中。
    3 句短 ref：任一单句（2字）与 input（6字）ratio=2*2/(2+6)=0.5 < 阈值，
    仅整体拼接（6字 vs 6字）=1.0 命中，从而真正隔离 join 路径。"""
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好。", "我是。", "助手。"]
    assert s._is_echo("你好我是助手") is True


async def test_is_echo_respects_disable_switch(monkeypatch):
    monkeypatch.setattr("app.config.ENABLE_ECHO_DETECT", False)
    s, _ = _make_session()
    s.state = "speaking"
    s._echo_ref = ["你好。"]
    assert s._is_echo("你好") is False
