import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.session import Session


def _make_session():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.send_bytes = AsyncMock()
    loop = asyncio.get_event_loop()
    s = Session(ws, loop)
    s._running = True
    return s


async def test_request_photo_returns_data_when_resolved():
    s = _make_session()
    ws = s.ws

    async def fake_send(payload):
        # _send 下发的是 JSON 字符串；解析后模拟前端回传 photo
        obj = json.loads(payload)
        if obj.get("type") != "take_photo":
            return
        call_id = obj["call_id"]
        asyncio.get_event_loop().call_later(
            0.05, lambda: asyncio.ensure_future(s.resolve_photo(call_id, "data:image/jpeg;base64,X"))
        )

    ws.send_text = fake_send
    data = await s.request_photo("c1")
    assert data == "data:image/jpeg;base64,X"
    assert "c1" not in s._pending_photos


async def test_request_photo_timeout_returns_none(monkeypatch):
    s = _make_session()
    monkeypatch.setattr("app.config.TAKE_PHOTO_TIMEOUT", 0)  # 立即超时
    s.ws.send_text = AsyncMock()
    data = await s.request_photo("c2")
    assert data is None


async def test_resolve_photo_ignores_unknown_call_id():
    s = _make_session()
    await s.resolve_photo("nope", "data")  # 不应抛错
    assert s._pending_photos == {}
