"""McpToolAdapter：把单个 MCP tool 包装成 Tool 协议，复用 tool-calling 循环。"""
from __future__ import annotations

from ..tools.base import Tool, ToolContext, ToolResult


class McpToolAdapter:
    """实现 Tool 协议。schema 取自 MCP tool；execute 转发给共享的 McpClient。"""

    def __init__(self, mcp_tool, client) -> None:
        self._tool = mcp_tool
        self._client = client

    @property
    def schema(self) -> dict:
        return {
            "name": self._tool.name,
            "description": self._tool.description or f"MCP tool {self._tool.name}",
            "parameters": self._tool.inputSchema or {"type": "object", "properties": {}},
        }

    async def execute(self, ctx: ToolContext) -> ToolResult:
        text = await self._client.call_tool(self._tool.name, ctx.args)
        return ToolResult(text=text)
