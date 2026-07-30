"""工具注册表：管理内置/外部工具，提供 schema 列表与统一执行入口。

工具带来源标记（source），便于按来源批量增删——例如会话级屏蔽 MCP 工具。
"""
from __future__ import annotations

import logging

from .base import Tool, ToolContext, ToolResult

log = logging.getLogger("demotalk.tools")


class ToolRegistry:
    def __init__(self) -> None:
        # name -> (tool, source)；source 如 "builtin" / "mcp"
        self._tools: dict[str, tuple[Tool, str]] = {}

    def register(self, tool: Tool, source: str = "builtin") -> None:
        self._tools[tool.schema["name"]] = (tool, source)

    def unregister(self, name: str) -> None:
        """按名移除单个工具；不存在则空操作。"""
        self._tools.pop(name, None)

    def clear_by_source(self, source: str) -> None:
        """移除指定来源的全部工具（如屏蔽所有 MCP 工具）。"""
        self._tools = {n: v for n, v in self._tools.items() if v[1] != source}

    def get(self, name: str) -> Tool | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def sources(self) -> dict[str, str]:
        """name -> source 映射，供判断某来源是否存在工具。"""
        return {n: s for n, (_, s) in self._tools.items()}

    def schemas(self) -> list[dict]:
        """OpenAI chat completions 的 tools 参数格式。"""
        return [{"type": "function", "function": t.schema} for (t, _s) in self._tools.values()]

    async def execute(self, name: str, ctx: ToolContext) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(text=f"未知工具：{name}")
        try:
            return await tool.execute(ctx)
        except Exception as e:
            log.exception("工具 %s 执行异常", name)
            return ToolResult(text=f"工具执行出错：{e}")
