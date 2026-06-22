"""内置工具：拍照。经 ToolContext.request_photo 让前端拍照并回传。"""
from __future__ import annotations

from ..base import Tool, ToolContext, ToolResult


class TakePhotoTool:
    """拍一张当前摄像头画面，用于回答需要视觉的问题。"""

    @property
    def schema(self) -> dict:
        return {
            "name": "take_photo",
            "description": "拍一张当前摄像头画面，用于回答需要视觉的问题（如「这是什么」「前面有什么」「前面有几种颜色」）。需要看用户周围环境时调用。",
            "parameters": {"type": "object", "properties": {}},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        data_url = await ctx.request_photo(ctx.call_id)
        if not data_url:
            return ToolResult(text="拍照失败或超时，无法获取画面。")
        return ToolResult(text="拍到的照片", image_data_url=data_url)
