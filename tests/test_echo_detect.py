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
