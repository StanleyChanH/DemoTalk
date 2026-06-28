from app.tools.base import Tool, ToolContext, ToolResult


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


def test_tool_protocol_is_protocol():
    # Tool 是 Protocol，可被任意含 schema/execute 的对象满足
    class Echo:
        @property
        def schema(self) -> dict:
            return {"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}}}

        async def execute(self, ctx: ToolContext) -> "ToolResult":
            return ToolResult(text="ok")

    e = Echo()
    assert isinstance(e, Tool)  # runtime_checkable 协议校验


def test_tool_context_fields():
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    assert ctx.call_id == "c1"
    assert ctx.args == {}
    assert callable(ctx.request_photo)


def test_tool_context_end_conversation_optional_default():
    # 不传 request_end_conversation 时默认 None（兼容 MCP 适配器等不使用的 tool）
    ctx = ToolContext(call_id="c1", args={}, request_photo=lambda cid: None)
    assert ctx.request_end_conversation is None


async def test_tool_context_end_conversation_can_be_injected():
    async def fake_end():
        return None

    ctx = ToolContext(
        call_id="c1",
        args={},
        request_photo=lambda cid: None,
        request_end_conversation=fake_end,
    )
    assert callable(ctx.request_end_conversation)
    await ctx.request_end_conversation()  # 可 await
