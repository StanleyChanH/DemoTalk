"""内置工具：结束对话。用户明确表达结束意图时由 LLM 调用，
经 ToolContext.request_end_conversation 触发会话优雅关闭。"""
from __future__ import annotations

from ..base import ToolContext, ToolResult


class EndConversationTool:
    """用户明确要结束对话时调用，触发会话收尾（告别语播完后断连）。"""

    @property
    def schema(self) -> dict:
        return {
            "name": "end_conversation",
            "description": "当用户明确表达要结束对话时调用（如『再见』『拜拜』『结束对话』『不聊了』『先这样吧』『挂了』）。调用前可用一句话简短告别。",
            "parameters": {"type": "object", "properties": {}},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        if ctx.request_end_conversation:
            await ctx.request_end_conversation()
        return ToolResult(text="(对话已结束)")  # 仅留作历史；结束分支不再调 LLM
