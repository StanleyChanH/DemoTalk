"""句子切分细化测试：逗号/冒号切分、长度兜底、ENABLE_COMMA_SPLIT 回退。

通过 _run_turn 喂 TTS 的每段（累积进 _echo_ref）观察切分行为。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app import config
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_running_loop()
    s = Session(ws, loop)
    s._running = True
    return s, ws


def _stub_tts(monkeypatch):
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


def _set_llm_chunks(s, chunks, full=None):
    """让 astream_once 按 chunks 分多次 yield（模拟真实流式）。"""
    full = full if full is not None else "".join(chunks)

    async def fake_astream_once(tools=None):
        s.llm._history.append({"role": "assistant", "content": full})
        for c in chunks:
            yield {"type": "text", "text": c}
        yield {"type": "done", "tool_calls": [], "finish_reason": "stop"}

    s.llm.astream_once = fake_astream_once
    s.llm.add_user = lambda t: None
    s.llm.add_tool = lambda cid, c: None


async def test_comma_split_feeds_clauses(monkeypatch):
    """ENABLE_COMMA_SPLIT=True：逗号也切，首句拆成更小子句，更早喂 TTS。"""
    _stub_tts(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_COMMA_SPLIT", True)
    monkeypatch.setattr(config, "SENTENCE_SPLIT_MAX_LEN", 12)
    s, _ = _make_session()
    s._current_turn = 1
    _set_llm_chunks(s, ["你好，我是助手，很高兴认识你。"])

    await s._run_turn("嗨", turn=1)

    assert s._echo_ref == ["你好，", "我是助手，", "很高兴认识你。"]


async def test_strict_split_keeps_comma_intact(monkeypatch):
    """ENABLE_COMMA_SPLIT=False：逗号不切，整句到句号才喂（回退原行为）。"""
    _stub_tts(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_COMMA_SPLIT", False)
    monkeypatch.setattr(config, "SENTENCE_SPLIT_MAX_LEN", 12)
    s, _ = _make_session()
    s._current_turn = 1
    _set_llm_chunks(s, ["你好，我是助手，很高兴认识你。"])

    await s._run_turn("嗨", turn=1)

    assert s._echo_ref == ["你好，我是助手，很高兴认识你。"]


async def test_length_fallback_flushes_long_punctless(monkeypatch):
    """无标点但累积超长：长度兜底强制 flush，避免一直攒着不喂 TTS。"""
    _stub_tts(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_COMMA_SPLIT", True)
    monkeypatch.setattr(config, "SENTENCE_SPLIT_MAX_LEN", 5)  # 短阈值便于触发
    s, _ = _make_session()
    s._current_turn = 1
    _set_llm_chunks(s, ["一二三", "四五六", "七八九", "十"])  # 分块流式

    await s._run_turn("嗨", turn=1)

    # 阈值 5：buffer 到 6 时 flush 前 5（"一二三四五六"），尾"七八九十"循环外 feed
    assert "".join(s._echo_ref) == "一二三四五六七八九十"
    assert len(s._echo_ref) >= 2  # 被长度兜底切成多段
    assert all(seg for seg in s._echo_ref)  # 无空段


async def test_length_fallback_disabled_when_zero(monkeypatch):
    """SENTENCE_SPLIT_MAX_LEN=0：禁用长度兜底，无标点则整段循环外一次性 feed。"""
    _stub_tts(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_COMMA_SPLIT", True)
    monkeypatch.setattr(config, "SENTENCE_SPLIT_MAX_LEN", 0)
    s, _ = _make_session()
    s._current_turn = 1
    _set_llm_chunks(s, ["一二三四五六七八九十"])  # 无标点

    await s._run_turn("嗨", turn=1)

    assert s._echo_ref == ["一二三四五六七八九十"]
