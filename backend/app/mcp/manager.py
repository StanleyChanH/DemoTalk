"""McpManager：进程级管理多个 MCP server 连接，把 tools 注册进 ToolRegistry。"""
from __future__ import annotations

import logging

from ..tools.registry import ToolRegistry
from .adapter import McpToolAdapter
from .client import McpClient
from .config import ServerConfig

log = logging.getLogger("demotalk.mcp")


class McpManager:
    def __init__(self) -> None:
        self._clients: list[McpClient] = []
        self._adapters: list[McpToolAdapter] = []

    def _make_client(self, cfg: ServerConfig) -> McpClient:
        """工厂方法（便于测试覆盖）。"""
        return McpClient(cfg)

    async def load_all(self, configs: list[ServerConfig]) -> None:
        """连所有 server；单个失败跳过，不影响其他。"""
        for cfg in configs:
            client = self._make_client(cfg)
            try:
                await client.connect()
                tools = await client.list_tools()
            except Exception as e:
                log.warning("MCP server %s 连接失败，跳过：%s", cfg.name, e)
                try:
                    await client.close()
                except Exception:
                    pass
                continue
            self._clients.append(client)
            for t in tools:
                self._adapters.append(McpToolAdapter(t, client))
            log.info("MCP server %s 注册了 %d 个工具", cfg.name, len(tools))

    def register_into(self, registry: ToolRegistry) -> None:
        """把所有 adapter 注册进给定 registry（每 session 调一次），标记来源 mcp。

        命名：MCP tool 用其原始 name（通常已带 server 前缀，如 mcp_howtocook_*），
        调用方需保证跨 server / 与 take_photo 唯一；本方法不做冲突降级。
        """
        for a in self._adapters:
            registry.register(a, source="mcp")

    def has_tools(self) -> bool:
        """是否加载到了可用 MCP 工具（供下发 mcp_available）。"""
        return len(self._adapters) > 0

    async def close_all(self) -> None:
        for c in self._clients:
            try:
                await c.close()
            except Exception:
                log.debug("MCP client 关闭异常", exc_info=True)
        self._clients = []
        self._adapters = []


# 进程级单例
mcp_manager = McpManager()
