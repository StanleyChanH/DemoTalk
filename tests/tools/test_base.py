from app.tools.base import ToolResult


def test_text_only_result():
    r = ToolResult(text="hello")
    assert r.to_message_content() == [{"type": "text", "text": "hello"}]


def test_image_result():
    r = ToolResult(text="照片", image_data_url="data:image/jpeg;base64,AAA")
    content = r.to_message_content()
    assert content[0] == {"type": "text", "text": "照片"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}


def test_empty_result():
    r = ToolResult()
    assert r.to_message_content() == [{"type": "text", "text": ""}]
