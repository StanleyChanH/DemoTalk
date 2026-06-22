"""工具注册表：管理内置/外部工具，提供 schema 列表与统一执行入口。"""
from __future__ import annotations

import logging

from .base import Tool, ToolContext, ToolResult

log = logging.getLogger("demotalk.tools")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.schema["name"]] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """OpenAI chat completions 的 tools 参数格式。"""
        return [{"type": "function", "function": t.schema} for t in self._tools.values()]

    async def execute(self, name: str, ctx: ToolContext) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(text=f"未知工具：{name}")
        try:
            return await tool.execute(ctx)
        except Exception as e:
            log.exception("工具 %s 执行异常", name)
            return ToolResult(text=f"工具执行出错：{e}")
