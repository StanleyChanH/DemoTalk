"""端到端延迟埋点测试：首包下发 latency_metric，每轮一次，可关闭。"""
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


async def test_latency_metric_emitted_on_first_audio(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LATENCY_METRIC", True)
    s, _ = _make_session()
    tts = MagicMock()
    s.tts = tts
    s._t_turn_start = asyncio.get_running_loop().time()
    sent = []

    async def cap(obj):
        sent.append(obj)

    s._send = cap

    await s._on_tts_audio(b"\x00\x00", tts)

    metric = next((o for o in sent if o.get("type") == "latency_metric"), None)
    assert metric is not None
    for key in ("total_ms", "tts_first_ms", "llm_ttft_ms"):
        assert key in metric


async def test_latency_metric_emitted_once_per_turn(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LATENCY_METRIC", True)
    s, _ = _make_session()
    tts = MagicMock()
    s.tts = tts
    s._t_turn_start = asyncio.get_running_loop().time()
    sent = []

    async def cap(obj):
        sent.append(obj)

    s._send = cap

    await s._on_tts_audio(b"\x00", tts)
    await s._on_tts_audio(b"\x00", tts)

    metrics = [o for o in sent if o.get("type") == "latency_metric"]
    assert len(metrics) == 1  # 本轮只发一次


async def test_latency_metric_disabled(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LATENCY_METRIC", False)
    s, _ = _make_session()
    tts = MagicMock()
    s.tts = tts
    s._t_turn_start = asyncio.get_running_loop().time()
    sent = []

    async def cap(obj):
        sent.append(obj)

    s._send = cap

    await s._on_tts_audio(b"\x00", tts)

    assert all(o.get("type") != "latency_metric" for o in sent)


async def test_latency_metric_includes_llm_ttft(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LATENCY_METRIC", True)
    s, _ = _make_session()
    tts = MagicMock()
    s.tts = tts
    loop = asyncio.get_running_loop()
    s._t_turn_start = loop.time()
    s._t_llm_first = loop.time() + 0.3  # 模拟 LLM 首字延迟 300ms
    sent = []

    async def cap(obj):
        sent.append(obj)

    s._send = cap

    await s._on_tts_audio(b"\x00", tts)

    metric = next(o for o in sent if o.get("type") == "latency_metric")
    assert metric["llm_ttft_ms"] is not None
    assert metric["llm_ttft_ms"] >= 250  # 约 300ms（留容差）
