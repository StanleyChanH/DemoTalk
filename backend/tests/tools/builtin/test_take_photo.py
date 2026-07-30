import pytest
from app.tools.base import ToolContext
from app.tools.builtin.take_photo import TakePhotoTool


@pytest.fixture
def tool():
    return TakePhotoTool()


def test_schema(tool):
    s = tool.schema
    assert s["name"] == "take_photo"
    assert s["parameters"] == {"type": "object", "properties": {}}


async def test_execute_returns_image_when_photo_ok(tool):
    async def fake_request(call_id):
        assert call_id == "c1"
        return "data:image/jpeg;base64,AAA"

    ctx = ToolContext(call_id="c1", args={}, request_photo=fake_request)
    result = await tool.execute(ctx)
    assert result.image_data_url == "data:image/jpeg;base64,AAA"
    content = result.to_message_content()
    assert any(c.get("type") == "image_url" for c in content)


async def test_execute_returns_text_when_photo_none(tool):
    async def fake_request(call_id):
        return None

    ctx = ToolContext(call_id="c1", args={}, request_photo=fake_request)
    result = await tool.execute(ctx)
    assert result.image_data_url is None
    assert "失败" in result.text or "超时" in result.text
