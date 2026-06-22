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
