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
    "你是一个简洁友好的中文语音助手。请用 1-2 句话简短回答，口语化、适合语音播报，不要使用 markdown 或列表。当需要看用户周围画面时（例如用户问『这是什么』『前面有什么』），先调用 take_photo 拍照再回答。当用户明确表达结束对话（如『再见／拜拜／结束对话／不聊了／先这样吧／挂了』）时，可先用一句话简短告别，并调用 end_conversation 工具结束对话；无明确结束意图时不要调用。",
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

# ---- VAD（前端语音门控灵敏度，0-100，越大越灵敏；默认 70 偏灵敏，缩短开口确认延迟）----
VAD_SENSITIVITY: int = _int("VAD_SENSITIVITY", 70)

# ---- 回声检测（外放自循环防护；后端文本级，比对 STT final 与最近 TTS 文本）----
ENABLE_ECHO_DETECT: bool = _bool("ENABLE_ECHO_DETECT", True)
ECHO_SIMILARITY_THRESHOLD: float = _float("ECHO_SIMILARITY_THRESHOLD", 0.6)
ECHO_HANGOVER_MS: int = _int("ECHO_HANGOVER_MS", 1200)

# ---- 空闲超时（长时间无输入自动播报提示并断开）----
ENABLE_IDLE_TIMEOUT: bool = _bool("ENABLE_IDLE_TIMEOUT", True)
IDLE_TIMEOUT: int = _int("IDLE_TIMEOUT", 15)  # 秒，listening 期间无活动达此值则触发
IDLE_PROMPT: str = _get(
    "IDLE_PROMPT",
    "你好像暂时不需要我了，我先挂啦，需要时随时叫我。",
)

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
# 是否启用「语义结束对话」（用户说再见等由 LLM 调 end_conversation 工具结束）
ENABLE_END_BY_VOICE: bool = _bool("ENABLE_END_BY_VOICE", True)

# ---- MCP ----
ENABLE_MCP: bool = _bool("ENABLE_MCP", True)
MCP_CONFIG_FILE: str = _get("MCP_CONFIG_FILE", "mcp.json")

# ---- 低延迟优化（参考 speech-to-speech；每项独立开关，可回滚）----
# 端到端延迟埋点：每轮首包下发 {total_ms, tts_first_ms, llm_ttft_ms} 到前端，量化每次改动收益
ENABLE_LATENCY_METRIC: bool = _bool("ENABLE_LATENCY_METRIC", True)
# 句子切分细化：LLM 输出按逗号/冒号等子句切分喂 TTS，更早出首包（false 退回仅句末标点）
ENABLE_COMMA_SPLIT: bool = _bool("ENABLE_COMMA_SPLIT", True)
# 句子切分长度兜底：无标点命中时累积到此字数也强制 flush 一段（0=禁用）
SENTENCE_SPLIT_MAX_LEN: int = _int("SENTENCE_SPLIT_MAX_LEN", 12)
# 前端 VAD 驱动 turn-end：用 onSpeechEnd 提前触发（替代等 STT 句末 final 800ms），首响路径最大优化
ENABLE_VAD_TURN_END: bool = _bool("ENABLE_VAD_TURN_END", True)
# 前端本地 barge-in：speaking 期 VAD 检测到开口即本地停播 + 上行 cancel（false 回退纯服务端）
ENABLE_LOCAL_BARGE_IN: bool = _bool("ENABLE_LOCAL_BARGE_IN", True)
