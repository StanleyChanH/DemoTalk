from app import config


def test_vision_defaults():
    assert config.ENABLE_VISION is True
    assert config.PHOTO_MAX_SIZE == 640
    assert config.PHOTO_QUALITY == 0.8
    assert config.TAKE_PHOTO_TIMEOUT == 5
    assert config.MAX_TOOL_CALLS_PER_TURN == 3
