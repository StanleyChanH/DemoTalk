"""MCP 配置：读取 mcp.json，解析为 ServerConfig 列表。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("demotalk.mcp")


@dataclass
class ServerConfig:
    name: str
    type: str  # "sse" | "stdio"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict | None = None


def load_mcp_config(path: str) -> list[ServerConfig]:
    """读 mcp.json。文件不存在或格式错返回空列表（不抛异常）。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("mcp.json 解析失败：%s", e)
        return []
    raw = data.get("mcpServers", {}) or {}
    servers: list[ServerConfig] = []
    for name, cfg in raw.items():
        cfg = cfg or {}
        t = cfg.get("type", "sse")
        servers.append(ServerConfig(
            name=name,
            type=t,
            url=cfg.get("url"),
            command=cfg.get("command"),
            args=cfg.get("args"),
            env=cfg.get("env"),
        ))
    return servers
