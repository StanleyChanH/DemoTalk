"""FastAPI 后端入口：纯 WebSocket 实时语音助手 API（/ws + /healthz）。

前后端分离架构：前端由独立的 nginx 容器提供静态资源，浏览器**直连**本服务 /ws
（前端通过 DEMOTALK_BACKEND_URL 配置后端地址）。本服务不再托管静态前端。
"""
from __future__ import annotations

import asyncio
import json
import logging

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from contextlib import asynccontextmanager

from . import config
from .mcp.config import load_mcp_config as _load_mcp_config
from .mcp.manager import mcp_manager
from .session import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("demotalk")

@asynccontextmanager
async def lifespan(app):
    if config.ENABLE_MCP:
        servers = _load_mcp_config(config.MCP_CONFIG_FILE)
        await mcp_manager.load_all(servers)
    yield
    await mcp_manager.close_all()


app = FastAPI(title="DemoTalk", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root():
    """服务信息。前后端分离后根路径不再返回前端页面（前端在独立容器）。"""
    return JSONResponse({"service": "demotalk-backend", "ws": "/ws", "health": "/healthz"})


@app.get("/healthz")
async def healthz():
    return JSONResponse(
        {
            "ok": bool(config.DASHSCOPE_API_KEY),
            "models": {
                "stt": config.STT_MODEL,
                "llm": config.LLM_MODEL,
                "tts": config.TTS_MODEL,
                "voice": config.TTS_VOICE,
            },
            "tts_sample_rate": config.TTS_SAMPLE_RATE,
            "barge_in": config.ENABLE_BARGE_IN,
            "mcp": config.ENABLE_MCP,
            "end_by_voice": config.ENABLE_END_BY_VOICE,
        }
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    session = Session(ws, loop)

    if not config.DASHSCOPE_API_KEY:
        await ws.send_text(
            json.dumps(
                {"type": "error", "message": "未配置 DASHSCOPE_API_KEY，请在 .env 中设置后重启。"},
                ensure_ascii=False,
            )
        )
        await ws.close()
        return

    await session.start()
    log.info("新会话已建立")
    try:
        while True:
            msg = await ws.receive()
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            data_bytes = msg.get("bytes")
            data_text = msg.get("text")
            if data_bytes is not None:
                # 浏览器麦克风 16kHz/16bit/单声道 PCM
                await session.feed_mic(data_bytes)
            elif data_text is not None:
                try:
                    obj = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "stop":
                    break
                elif t == "photo":
                    await session.handle_photo(obj.get("call_id", ""), obj.get("data"))
                elif t == "photo_error":
                    await session.handle_photo_error(obj.get("call_id", ""))
                elif t == "set_flags":
                    await session.set_flags(obj)
                else:
                    await session.handle_control(obj)
    except WebSocketDisconnect:
        log.info("客户端断开")
    except Exception:
        log.exception("WebSocket 处理异常")
    finally:
        await session.shutdown()
        try:
            await ws.close()
        except Exception:
            pass
        log.info("会话已结束")


def run() -> None:
    """供 `uv run demotalk` 调用。"""
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()
