from app import config


def test_vision_defaults():
    assert config.ENABLE_VISION is True
    assert config.PHOTO_MAX_SIZE == 640
    assert config.PHOTO_QUALITY == 0.8
    assert config.TAKE_PHOTO_TIMEOUT == 5
    assert config.MAX_TOOL_CALLS_PER_TURN == 3


def test_mcp_defaults():
    assert config.ENABLE_MCP is True
    assert config.MCP_CONFIG_FILE == "mcp.json"


def test_end_by_voice_defaults():
    assert config.ENABLE_END_BY_VOICE is True


def test_echo_detect_defaults():
    assert config.ENABLE_ECHO_DETECT is True
    assert config.ECHO_SIMILARITY_THRESHOLD == 0.6
    assert config.ECHO_HANGOVER_MS == 1200
