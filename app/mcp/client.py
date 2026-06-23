"""McpClient：连接单个 MCP server（SSE 或 stdio），暴露 list_tools / call_tool。"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from .config import ServerConfig

log = logging.getLogger("demotalk.mcp")


class McpClient:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._session: ClientSession | None = None
        self._stack = AsyncExitStack()

    async def connect(self) -> None:
        """建立连接并 initialize。失败抛异常（由 manager 捕获跳过）。"""
        if self.config.type == "sse":
            if not self.config.url:
                raise ValueError(f"MCP server {self.config.name} 缺 url")
            read, write = await self._stack.enter_async_context(sse_client(self.config.url))
        elif self.config.type == "stdio":
            if not self.config.command:
                raise ValueError(f"MCP server {self.config.name} 缺 command")
            params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args or [],
                env=self.config.env,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        else:
            raise ValueError(f"MCP server {self.config.name} 未知 type: {self.config.type}")
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        log.info("MCP server 已连接: %s (%s)", self.config.name, self.config.type)

    async def list_tools(self) -> list:
        return (await self._session.list_tools()).tools

    async def call_tool(self, name: str, args: dict | None) -> str:
        """调用工具，拼接所有 TextContent 的 text 返回。"""
        result = await self._session.call_tool(name, args or {})
        texts = [getattr(c, "text", "") for c in result.content]
        return "\n".join(t for t in texts if t)

    async def close(self) -> None:
        await self._stack.aclose()
        self._session = None
