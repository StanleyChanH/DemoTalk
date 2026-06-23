from unittest.mock import AsyncMock, MagicMock


async def test_lifespan_loads_mcp_when_enabled(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.config, "ENABLE_MCP", True)
    monkeypatch.setattr(main, "_load_mcp_config", lambda p: [])
    main.mcp_manager.load_all = AsyncMock()
    main.mcp_manager.close_all = AsyncMock()

    async with main.lifespan(main.app):
        pass
    main.mcp_manager.load_all.assert_awaited_once()
    main.mcp_manager.close_all.assert_awaited_once()


async def test_lifespan_skips_when_disabled(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.config, "ENABLE_MCP", False)
    main.mcp_manager.load_all = AsyncMock()
    async with main.lifespan(main.app):
        pass
    main.mcp_manager.load_all.assert_not_awaited()
