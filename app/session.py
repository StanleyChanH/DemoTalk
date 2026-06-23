"""Session：每条 WebSocket 连接的会话编排器（状态机 + 三服务联动）。

状态：listening → thinking → speaking → listening ...
- STT 增量/句末 → 转发前端 / 触发一轮 LLM
- LLM 流式 delta → 前端打字机 + 按句喂 TTS
- TTS 流式 PCM → 前端播放
- barge-in：speaking 时收到新的句末文本 → 取消当前 TTS、停前端播放、开新一轮

跨线程：STT/TTS 回调在 SDK 线程，用 asyncio.run_coroutine_threadsafe 投递。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import WebSocket

from . import config
from .llm import LLMService
from .stt import STTService
from .tts import TTSService

log = logging.getLogger("demotalk.session")

# 句子结束符：中英文标点 + 换行。用于把 LLM 增量切成可立即合成的小段
_SENTENCE_END = re.compile(r"[。！？!?；;\n]")


class Session:
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self.ws = ws
        self.loop = loop
        self.stt = STTService(
            on_partial=self._on_partial,
            on_final=self._on_final,
            loop=loop,
        )
        self.llm = LLMService()
        self.tts: TTSService | None = None

        from .tools.registry import ToolRegistry
        from .tools.builtin.take_photo import TakePhotoTool

        self.tool_registry = ToolRegistry()
        if config.ENABLE_VISION:
            self.tool_registry.register(TakePhotoTool())
        # 待回传的拍照请求：call_id -> Future
        self._pending_photos: dict[str, asyncio.Future] = {}

        self.state = "idle"
        # turn 计数：barge-in 时自增，使进行中的旧轮失效
        self._current_turn = 0
        self._running = True
        # 当前回合的 asyncio.Task，便于 shutdown 时取消/回收
        self._turn_task: asyncio.Task | None = None

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        # 通知前端音频格式，便于解码播放
        await self._send(
            {
                "type": "tts_format",
                "sample_rate": config.TTS_SAMPLE_RATE,
                "encoding": "s16",
                "channels": 1,
            }
        )
        await self._set_state("listening")
        # 在事件循环线程里启动 SDK（其内部会开 WS 线程）
        self.stt.start()

    async def shutdown(self) -> None:
        self._running = False
        # 先取消进行中的回合任务，避免它继续操作已关闭的 ws / 触发 TTS
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                log.debug("shutdown 取消回合任务异常", exc_info=True)
        # STT.stop 可能阻塞等待 task-finished，放到线程池避免卡事件循环
        try:
            await self.loop.run_in_executor(None, self.stt.stop)
        except Exception:
            log.exception("STT stop 异常")
        if self.tts is not None:
            try:
                self.tts.cancel()
            except Exception:
                pass
            self.tts = None

    # ---------- 来自 WS 的输入 ----------

    async def feed_mic(self, pcm: bytes) -> None:
        self.stt.feed(pcm)

    async def handle_control(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "cancel":
            await self._barge_in()
        # stop 由 WS 层处理（断开）

    # ---------- STT 回调（在 SDK 线程投递过来的协程里执行）----------

    async def _on_partial(self, text: str) -> None:
        if not self._running:
            return
        await self._send({"type": "partial", "text": text})

    async def _on_final(self, text: str) -> None:
        if not self._running:
            return
        text = text.strip()
        if not text:
            return
        await self._send({"type": "user_final", "text": text})

        if self.state == "speaking":
            if config.ENABLE_BARGE_IN:
                await self._barge_in()
            else:
                # 不打断：忽略说话期间的输入
                return

        self._current_turn += 1
        turn = self._current_turn
        self._turn_task = asyncio.create_task(self._run_turn(text, turn))

    # ---------- 一轮对话 ----------

    async def _run_turn(self, user_text: str, turn: int) -> None:
        tts: TTSService | None = None
        try:
            await self._set_state("thinking")
            tts = TTSService(
                on_audio=self._on_tts_audio,
                on_state=self._on_tts_state,
                loop=self.loop,
            )
            self.tts = tts
            tts.start()

            def active() -> bool:
                return turn == self._current_turn and self._running

            self.llm.add_user(user_text)
            tools = self.tool_registry.schemas() if config.ENABLE_VISION else None

            for _ in range(config.MAX_TOOL_CALLS_PER_TURN):
                buffer = ""
                tool_calls: list[dict] = []
                async for event in self.llm.astream_once(tools=tools):
                    if not active():
                        tts.cancel()
                        return
                    if event["type"] == "text":
                        delta = event["text"]
                        await self._send({"type": "delta", "text": delta})
                        buffer += delta
                        while True:
                            m = _SENTENCE_END.search(buffer)
                            if not m:
                                break
                            sentence = buffer[: m.end()]
                            buffer = buffer[m.end():]
                            if active():
                                tts.feed(sentence)
                    elif event["type"] == "done":
                        tool_calls = event.get("tool_calls", [])
                if not active():
                    tts.cancel()
                    return
                if buffer.strip() and active():
                    tts.feed(buffer)
                if not tool_calls:
                    if active():
                        tts.finish()
                    break  # finish_reason=stop
                # 执行本轮所有 tool_calls
                for tc in tool_calls:
                    if not active():
                        tts.cancel()
                        return
                    call_id = tc.get("id", "")
                    name = tc.get("function", {}).get("name", "")
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    from .tools.base import ToolContext
                    ctx = ToolContext(call_id=call_id, args=args, request_photo=self.request_photo)
                    await self._send({"type": "tool_running", "tool": name})
                    result = await self.tool_registry.execute(name, ctx)
                    # 始终补上 tool result：astream_once 已 append assistant(tool_calls)，
                    # 必须紧跟 tool 响应，否则下次 API 调用会因 tool_calls 无响应报错。
                    self.llm.add_tool(call_id, result.to_message_content())
                    if not active():
                        tts.cancel()
                        return
                # 带 tool 结果进入下一轮 astream_once
            else:
                # 达到 MAX_TOOL_CALLS_PER_TURN，强制收尾
                if active():
                    tts.finish()
        except Exception:
            log.exception("_run_turn 异常")
            await self._send({"type": "error", "message": "本轮对话失败"})
            if tts is not None:
                try:
                    tts.cancel()
                except Exception:
                    pass
            await self._set_state("listening")

    async def _barge_in(self) -> None:
        log.info("barge-in：打断当前 TTS")
        self._current_turn += 1  # 使正在进行的轮次失效
        if self.tts is not None:
            self.tts.cancel()
            self.tts = None
        await self._send({"type": "cancel_playback"})
        await self._set_state("listening")

    # ---------- 拍照（tool 交互）----------

    async def request_photo(self, call_id: str) -> str | None:
        """发 take_photo 请求，等待前端回传；超时返回 None。"""
        fut = self.loop.create_future()
        self._pending_photos[call_id] = fut
        await self._send({"type": "take_photo", "call_id": call_id})
        try:
            return await asyncio.wait_for(fut, timeout=config.TAKE_PHOTO_TIMEOUT)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_photos.pop(call_id, None)

    async def resolve_photo(self, call_id: str, data: str | None) -> None:
        fut = self._pending_photos.get(call_id)
        if fut is not None and not fut.done():
            fut.set_result(data)

    async def handle_photo(self, call_id: str, data) -> None:
        await self.resolve_photo(call_id, data)

    async def handle_photo_error(self, call_id: str) -> None:
        await self.resolve_photo(call_id, None)

    # ---------- TTS 回调（在 SDK 线程投递过来的协程里执行）----------

    async def _on_tts_audio(self, data: bytes, source: TTSService) -> None:
        # 仅接收当前轮的音频（barge-in 后旧实例的音频丢弃）
        if source is not self.tts or not self._running:
            return
        try:
            await self.ws.send_bytes(data)
        except Exception:
            log.exception("下发 TTS 音频失败")

    async def _on_tts_state(self, event: str, source: TTSService) -> None:
        if source is not self.tts or not self._running:
            return
        if event == "tts_start":
            await self._send({"type": "tts_start"})
            await self._set_state("speaking")
        elif event == "tts_end":
            await self._send({"type": "tts_end"})
            await self._set_state("listening")
        elif event == "tts_error":
            await self._send({"type": "error", "message": "语音合成失败"})
            await self._send({"type": "tts_end"})  # 仍通知前端收尾（flush 打字机）
            await self._set_state("listening")

    # ---------- 辅助 ----------

    async def _set_state(self, state: str) -> None:
        self.state = state
        await self._send({"type": "state", "state": state})

    async def _send(self, obj: dict) -> None:
        if not self._running:
            return
        try:
            await self.ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:
            log.debug("下发消息失败（连接可能已关闭）", exc_info=True)
