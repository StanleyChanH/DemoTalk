"""STT：封装阿里云百炼 fun-asr-realtime 实时语音识别。

使用 dashscope.audio.asr.Recognition（SDK 内部维护 WebSocket 线程）。
本服务在 SDK 线程的回调里把结果桥接到 asyncio 事件循环。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from . import config

log = logging.getLogger("demotalk.stt")


# 回调签名：on_partial(text) / on_final(text)，均为 async
PartialCb = Callable[[str], Awaitable[None]]
FinalCb = Callable[[str], Awaitable[None]]


class STTService:
    """单连接、单会话使用；start() 后喂音频，stop() 后不可复用。"""

    def __init__(
        self,
        on_partial: PartialCb,
        on_final: FinalCb,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._loop = loop
        self._recognition: Recognition | None = None
        # 最新 partial 文本缓存：供前端 VAD speech_end 提前触发 turn-end 时取用（不等 STT final）
        self._last_partial: str = ""

        # 全局设置（SDK 读取），后续 TTS 也复用
        dashscope.api_key = config.DASHSCOPE_API_KEY
        dashscope.base_websocket_api_url = config.DASHSCOPE_WS_URL

    def start(self) -> None:
        svc = self

        class Callback(RecognitionCallback):
            def on_open(self) -> None:
                log.info("STT websocket 已连接")

            def on_event(self, result: RecognitionResult) -> None:
                sentence = result.get_sentence()
                text = (sentence or {}).get("text", "")
                if not text:
                    return
                is_final = RecognitionResult.is_sentence_end(sentence)
                if is_final:
                    # 句末：清空缓存，避免下一轮 speech_end 误用上一轮残留 partial
                    svc._last_partial = ""
                    coro = svc._on_final(text)
                else:
                    # 缓存最新 partial：前端 VAD speech_end 可据此提前结束回合
                    svc._last_partial = text
                    coro = svc._on_partial(text)
                # 从 SDK 线程把协程投递到事件循环
                asyncio.run_coroutine_threadsafe(coro, svc._loop)

            def on_error(self, result: RecognitionResult) -> None:
                log.error("STT 错误: %s (%s)", getattr(result, "message", "?"), getattr(result, "request_id", "?"))

            def on_complete(self) -> None:
                log.info("STT 会话结束")

            def on_close(self) -> None:
                log.info("STT websocket 已关闭")

        self._recognition = Recognition(
            model=config.STT_MODEL,
            format="pcm",
            sample_rate=16000,
            semantic_punctuation_enabled=False,  # VAD 模式：更低延迟，适合交互
            max_sentence_silence=config.MAX_SENTENCE_SILENCE,
            language_hints=config.STT_LANGUAGE_HINTS,
            callback=Callback(),
        )
        self._recognition.start()
        log.info("STT 已启动 (model=%s)", config.STT_MODEL)

    def feed(self, pcm: bytes) -> None:
        """喂入 16kHz/16bit/单声道 PCM 帧。线程安全（由 asyncio 线程调用）。"""
        if self._recognition is not None:
            self._recognition.send_audio_frame(pcm)

    @property
    def last_partial(self) -> str:
        """最新 STT partial 文本（供 VAD speech_end 提前触发 turn-end 时取用）。"""
        return self._last_partial

    def stop(self) -> None:
        rec = self._recognition
        self._recognition = None
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                log.exception("STT stop 异常")
