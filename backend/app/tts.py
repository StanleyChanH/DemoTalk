"""TTS：封装阿里云百炼 CosyVoice 流式语音合成。

使用 dashscope.audio.tts_v2.SpeechSynthesizer（WebSocket 回调流式）。
streaming_call / streaming_complete 是阻塞调用，因此放在独立的 worker 线程里执行；
合成器回调（on_data 等）运行在 SDK 自己的 WS 线程，通过 run_coroutine_threadsafe
把音频字节桥接到 asyncio 事件循环再下发到浏览器。

低延迟要点：format 选 PCM_24000HZ_MONO_16BIT（无 MP3 解码开销），首包延迟约 350ms。
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Awaitable, Callable

import dashscope
from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from . import config

log = logging.getLogger("demotalk.tts")

# 回调签名（均为 async）：on_audio(data:bytes, source) / on_state(event:str, source)
AudioCb = Callable[[bytes, "TTSService"], Awaitable[None]]
StateCb = Callable[[str, "TTSService"], Awaitable[None]]


def _audio_format() -> AudioFormat:
    rate = config.TTS_SAMPLE_RATE
    # 采样率与位深/声道都打包在 enum 名里，没有独立 sample_rate 参数
    if rate == 24000:
        return AudioFormat.PCM_24000HZ_MONO_16BIT
    if rate == 22050:
        return AudioFormat.PCM_22050HZ_MONO_16BIT
    if rate == 48000:
        return AudioFormat.PCM_48000HZ_MONO_16BIT
    if rate == 16000:
        return AudioFormat.PCM_16000HZ_MONO_16BIT
    log.warning("未知的 TTS_SAMPLE_RATE=%s，回退到 24000", rate)
    return AudioFormat.PCM_24000HZ_MONO_16BIT


class TTSService:
    """一轮对话使用一个实例（对应一个 SpeechSynthesizer / WS 连接）。

    用法：start() 启动 worker 线程 → feed(text) 多次喂入句子 → finish() 结束。
    cancel() 用于 barge-in：置取消标志并丢弃后续音频，尝试取消合成器。
    """

    def __init__(
        self,
        on_audio: AudioCb,
        on_state: StateCb,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._on_audio = on_audio
        self._on_state = on_state
        self._loop = loop
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._synth: SpeechSynthesizer | None = None
        self._cancelled = False
        self._started = False  # 是否已至少成功 streaming_call 过一次
        self._format = _audio_format()

        dashscope.api_key = config.DASHSCOPE_API_KEY
        dashscope.base_websocket_api_url = config.DASHSCOPE_WS_URL

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def start(self) -> None:
        self._cancelled = False
        self._thread = threading.Thread(target=self._run, name="tts-worker", daemon=True)
        self._thread.start()

    def feed(self, text: str) -> None:
        if text and not self._cancelled:
            self._q.put(text)

    def finish(self) -> None:
        self._q.put(None)

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._q.put(None)  # 唤醒 worker 结束
        synth = self._synth
        if synth is not None:
            try:
                # SDK 的取消方法（向服务端发 finish-task，关闭合成）
                synth.streaming_cancel()
            except Exception:
                pass

    def _run(self) -> None:
        svc = self

        class Callback(ResultCallback):
            def __init__(self) -> None:
                self._first = True

            def on_open(self) -> None:
                log.info("TTS websocket 已连接")

            def on_close(self) -> None:
                log.info("TTS websocket 已关闭")

            def on_data(self, data: bytes) -> None:
                if svc._cancelled:
                    return
                if self._first:
                    self._first = False
                    asyncio.run_coroutine_threadsafe(svc._on_state("tts_start", svc), svc._loop)
                asyncio.run_coroutine_threadsafe(svc._on_audio(data, svc), svc._loop)

            def on_event(self, message: str) -> None:
                # 句子级事件，这里不做处理
                pass

            def on_complete(self) -> None:
                log.info("TTS 合成完成")
                asyncio.run_coroutine_threadsafe(svc._on_state("tts_end", svc), svc._loop)

            def on_error(self, message: str) -> None:
                log.error("TTS 错误: %s", message)
                asyncio.run_coroutine_threadsafe(svc._on_state("tts_error", svc), svc._loop)

        try:
            synth = SpeechSynthesizer(
                model=config.TTS_MODEL,
                voice=config.TTS_VOICE,
                format=self._format,
                callback=Callback(),
            )
            self._synth = synth

            started = False
            while True:
                item = self._q.get()
                if item is None or self._cancelled:
                    break
                # streaming_call 首次会建立 WS（可能短暂阻塞）；阻塞前后都复查取消标志，
                # 缩小 barge-in 时 worker 卡在 streaming_call 的窗口
                if self._cancelled:
                    break
                synth.streaming_call(item)
                started = True
                self._started = True
                if self._cancelled:
                    break

            # 只有真正开始过（至少一次 streaming_call）才能 complete，
            # 否则 SDK 会抛 InvalidTask("speech synthesizer has not been started.")
            if not self._cancelled and started:
                # 阻塞直到剩余文本合成完成
                synth.streaming_complete()
        except Exception:
            log.exception("TTS worker 异常")
            asyncio.run_coroutine_threadsafe(self._on_state("tts_error", svc), self._loop)
