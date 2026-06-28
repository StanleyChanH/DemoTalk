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
