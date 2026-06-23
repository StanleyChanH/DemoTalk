import json
from pathlib import Path

from app.mcp.config import ServerConfig, load_mcp_config


def test_parse_sse(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "mcpServers": {
            "howtocook-mcp": {"type": "sse", "url": "https://example/sse"}
        }
    }), encoding="utf-8")
    servers = load_mcp_config(str(f))
    assert len(servers) == 1
    s = servers[0]
    assert s.name == "howtocook-mcp"
    assert s.type == "sse"
    assert s.url == "https://example/sse"


def test_parse_stdio(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "mcpServers": {
            "local": {"type": "stdio", "command": "npx", "args": ["-y", "x"], "env": {"K": "v"}}
        }
    }), encoding="utf-8")
    servers = load_mcp_config(str(f))
    s = servers[0]
    assert s.type == "stdio"
    assert s.command == "npx"
    assert s.args == ["-y", "x"]
    assert s.env == {"K": "v"}


def test_missing_file_returns_empty(tmp_path):
    assert load_mcp_config(str(tmp_path / "nope.json")) == []
