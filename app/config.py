"""配置：从 .env / 环境变量读取，集中管理。"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# 从项目根目录加载 .env（uv run 时工作目录为项目根）
load_dotenv()


def _get(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


# ---- 必填 ----
DASHSCOPE_API_KEY: str = _get("DASHSCOPE_API_KEY", "")

# ---- 模型 ----
STT_MODEL: str = _get("STT_MODEL", "fun-asr-realtime")
LLM_MODEL: str = _get("LLM_MODEL", "qwen3.7-plus")
TTS_MODEL: str = _get("TTS_MODEL", "cosyvoice-v3-flash")
TTS_VOICE: str = _get("TTS_VOICE", "longanyang")
TTS_SAMPLE_RATE: int = _int("TTS_SAMPLE_RATE", 24000)

# ---- LLM ----
LLM_SYSTEM_PROMPT: str = _get(
    "LLM_SYSTEM_PROMPT",
    "你是一个简洁友好的中文语音助手。请用 1-2 句话简短回答，口语化、适合语音播报，不要使用 markdown 或列表。当需要看用户周围画面时（例如用户问『这是什么』『前面有什么』），先调用 take_photo 拍照再回答。",
)
LLM_TEMPERATURE: float = _float("LLM_TEMPERATURE", 0.7)
LLM_BASE_URL: str = _get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
# 对话历史保留的最大轮数（不含 system），用于控制 token 与成本
LLM_HISTORY_TURNS: int = _int("LLM_HISTORY_TURNS", 8)

# ---- STT ----
MAX_SENTENCE_SILENCE: int = _int("MAX_SENTENCE_SILENCE", 800)
STT_LANGUAGE_HINTS: list[str] = ["zh", "en"]

# ---- 行为 ----
ENABLE_BARGE_IN: bool = _bool("ENABLE_BARGE_IN", True)

# ---- 服务 ----
HOST: str = _get("HOST", "127.0.0.1")
PORT: int = _int("PORT", 8000)

# ---- 百炼 WS 端点（北京）----
DASHSCOPE_WS_URL: str = _get("DASHSCOPE_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")

# ---- 视觉 / tool-calling ----
ENABLE_VISION: bool = _bool("ENABLE_VISION", True)
PHOTO_MAX_SIZE: int = _int("PHOTO_MAX_SIZE", 640)
PHOTO_QUALITY: float = _float("PHOTO_QUALITY", 0.8)
TAKE_PHOTO_TIMEOUT: int = _int("TAKE_PHOTO_TIMEOUT", 5)
MAX_TOOL_CALLS_PER_TURN: int = _int("MAX_TOOL_CALLS_PER_TURN", 3)
