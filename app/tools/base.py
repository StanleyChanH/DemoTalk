"""通用工具框架：ToolResult / ToolContext / Tool 协议。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """工具执行结果，可承载文本与（可选）图像。

    to_message_content() 转为 OpenAI tool message 的多模态 content 数组。
    """

    text: str = ""
    image_data_url: str | None = None  # 形如 data:image/jpeg;base64,...

    def to_message_content(self) -> list[dict]:
        content: list[dict] = []
        if self.text:
            content.append({"type": "text", "text": self.text})
        if self.image_data_url:
            content.append({"type": "image_url", "image_url": {"url": self.image_data_url}})
        if not content:
            content.append({"type": "text", "text": ""})
        return content


@dataclass
class ToolContext:
    """工具执行上下文。request_photo 由 session 注入（返回 data URL 或 None）。"""

    call_id: str
    args: dict
    request_photo: Callable[[str], Awaitable[str | None]]


@runtime_checkable
class Tool(Protocol):
    """工具协议：声明 schema，execute 执行并返回 ToolResult。"""

    @property
    def schema(self) -> dict:
        """OpenAI function schema: {name, description, parameters}。"""
        ...

    async def execute(self, ctx: ToolContext) -> ToolResult:
        ...
