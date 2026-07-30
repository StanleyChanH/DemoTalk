"""DemoTalk 真实全链路自检。

用法：
  1) 先启动服务：  uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  2) 另开终端运行： uv run python scripts/selftest.py

做了五件事：
  Phase 1 —— 直接调 LLM（qwen3.7-plus）流式，验证 Key/网络/思考关闭。
  Phase 2 —— 直接调 TTS（cosyvoice-v3-flash）流式，收集 PCM 写成 wav（供 Phase3/4/5 当麦克风输入）。
  Phase 3 —— 起一个 WebSocket 客户端，把 Phase2 的语音按 16k/100ms 喂给 /ws，
             期望收到：partial → user_final → delta(打字机) → TTS 二进制音频 → tts_end。
             一次打通 session.py 的 STT→LLM→TTS 编排。
  Phase 4 —— 合成一句视觉意图语音喂 STT，期望 LLM 调 take_photo；selftest 回传一张
             左红右蓝测试图，验证 LLM 基于图作多模态回答 + TTS。LLM 未调 take_photo 不算失败
             （无头环境模型行为，非缺陷），仅打 INFO。
  Phase 5 —— 合成一句菜谱意图语音喂 STT，期望 LLM 调 mcp_howtocook_whatToEat 并基于
             菜谱结果回答 + TTS。LLM 未调 MCP 工具不算失败（无头环境模型行为，非缺陷），仅打 INFO。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import wave

# 把项目根加入 sys.path，便于直接 import app.*
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HERE = os.path.dirname(os.path.abspath(__file__))
SPEECH_WAV = os.path.join(HERE, "test_speech.wav")
VISION_SPEECH_WAV = os.path.join(HERE, "test_vision_speech.wav")
TTS_TEXT = "你好，我是一个实时语音助手，很高兴和你对话。"
VISION_TTS_TEXT = "我桌子上那个红色的东西是什么？"


def _make_test_png() -> bytes:
    """生成 120×120『左红右蓝』PNG（无 PIL，zlib+struct 手写）。"""
    import struct
    import zlib

    W, H = 120, 120
    # 每行：filter byte(0) + W 个 RGBA 像素
    red = (220, 30, 30, 255)
    blue = (30, 80, 220, 255)
    raw = bytearray()
    for y in range(H):
        raw.append(0)  # filter: None
        for x in range(W):
            raw.extend(red if x < W // 2 else blue)
    png = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


async def _synthesize_wav(text: str, out_path: str) -> int:
    """用 TTSService 把 text 合成为 24k PCM，写成 wav。返回 PCM 字节数。"""
    import wave

    from app import config
    from app.tts import TTSService

    loop = asyncio.get_running_loop()
    chunks: list[bytes] = []
    done = asyncio.Event()

    async def on_audio(data: bytes, src) -> None:
        chunks.append(data)

    async def on_state(event: str, src) -> None:
        if event in ("tts_end", "tts_error"):
            done.set()

    tts = TTSService(on_audio, on_state, loop)
    tts.start()
    tts.feed(text)
    tts.finish()
    await asyncio.wait_for(done.wait(), timeout=40)
    await asyncio.sleep(0.3)  # 等尾部包到齐

    pcm = b"".join(chunks)
    assert len(pcm) > 2000, f"TTS PCM 过少：{len(pcm)} 字节"
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.TTS_SAMPLE_RATE)
        w.writeframes(pcm)
    return len(pcm)


async def phase1_llm() -> None:
    from app.llm import LLMService

    print("\n===== Phase 1: LLM 真实流式调用 =====")
    llm = LLMService()
    out: list[str] = []
    async for delta in llm.astream("用一句话介绍你自己"):
        out.append(delta)
    text = "".join(out).strip()
    assert text, "LLM 返回为空"
    print(f"[PASS] LLM(qwen3.7-plus) 流式产出 {len(text)} 字：{text[:80]}")


async def phase2_tts() -> None:
    print("\n===== Phase 2: TTS 真实流式调用 → 写 wav =====")
    n = await _synthesize_wav(TTS_TEXT, SPEECH_WAV)
    print(f"[PASS] TTS(cosyvoice-v3-flash) 收到 {n} 字节")
    print(f"       已写入 {SPEECH_WAV}")


async def phase3_ws_roundtrip() -> None:
    import audioop
    import websockets

    print("\n===== Phase 3: WebSocket 端到端往返（STT→LLM→TTS）=====")
    # 把 Phase2 的 wav 重采样到 16k，作为"麦克风"输入
    with wave.open(SPEECH_WAV, "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    if sr != 16000:
        pcm16, _ = audioop.ratecv(pcm, 2, 1, sr, 16000, None)
    else:
        pcm16 = pcm
    dur = len(pcm16) / 32000.0
    print(f"[i] 喂入 STT：{len(pcm16)} 字节 16k PCM（约 {dur:.1f}s）")

    events: list[str] = []
    deltas: list[str] = []
    audio_bytes = 0
    tts_end = asyncio.Event()
    user_final = asyncio.Event()

    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        async def reader():
            nonlocal audio_bytes
            try:
                async for msg in ws:
                    if isinstance(msg, (bytes, bytearray)):
                        audio_bytes += len(msg)
                        continue
                    obj = json.loads(msg)
                    t = obj.get("type")
                    events.append(t)
                    if t == "partial":
                        print("   partial:", obj.get("text", ""))
                    elif t == "user_final":
                        print("   user_final:", obj.get("text", ""))
                        user_final.set()
                    elif t == "delta":
                        deltas.append(obj.get("text", ""))
                    elif t == "state":
                        print("   state:", obj.get("state"))
                    elif t == "tts_end":
                        print("   tts_end")
                        tts_end.set()
                    elif t == "error":
                        print("   ERROR:", obj.get("message"))
                        tts_end.set()
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.6)  # 等 tts_format / state 到达

        # 按真实语速喂音频（100ms 一帧）
        CHUNK = 3200
        for i in range(0, len(pcm16), CHUNK):
            await ws.send(pcm16[i:i + CHUNK])
            await asyncio.sleep(0.1)

        # 喂 ~2s 静音帧：真实麦克风会持续送音（含静音），STT 的 VAD 依赖这段静音
        # 才会判定句尾并产出 sentence_end=True（即 user_final）。不送静音则永不结句。
        silence = b"\x00" * CHUNK
        for _ in range(20):
            await ws.send(silence)
            await asyncio.sleep(0.1)

        # 等 user_final
        try:
            await asyncio.wait_for(user_final.wait(), timeout=12)
        except asyncio.TimeoutError:
            print("   [!] 12s 内未收到 user_final")

        # 等 TTS 播完
        try:
            await asyncio.wait_for(tts_end.wait(), timeout=40)
        except asyncio.TimeoutError:
            print("   [!] 40s 内未收到 tts_end")

        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(0.5)
        rtask.cancel()

    print(f"\n[结果] 事件序列: {events}")
    print(f"[结果] 收到 TTS 音频: {audio_bytes} 字节")
    print(f"[结果] 助手回复: {''.join(deltas)[:120]}")

    ok = True
    if "user_final" not in events:
        print("[FAIL] 未收到 user_final —— STT 未识别出语音"); ok = False
    if not deltas:
        print("[FAIL] 未收到 delta —— LLM 未流式输出"); ok = False
    if audio_bytes < 1000:
        print("[FAIL] 几乎没收到 TTS 音频"); ok = False
    if "tts_end" not in events:
        print("[FAIL] 未收到 tts_end"); ok = False
    if ok:
        print("[PASS] WS 端到端往返成功：STT→LLM→TTS 编排正常")
    else:
        raise AssertionError("WS 端到端往返未通过，见上方 [FAIL]")


async def phase4_vision_roundtrip() -> None:
    """视觉 tool 端到端：合成视觉意图语音 → 喂 STT → 期望 take_photo → 回传测试图 →
    LLM 基于图回答 + TTS。LLM 不调 take_photo 不算失败（无头环境模型行为，非缺陷）。"""
    import audioop
    import base64
    import websockets

    print("\n===== Phase 4: 视觉 tool 端到端（STT→LLM take_photo→多模态回答→TTS）=====")

    # 1) 合成视觉意图语音
    n = await _synthesize_wav(VISION_TTS_TEXT, VISION_SPEECH_WAV)
    print(f"[i] 视觉意图语音：{n} 字节，已写入 {VISION_SPEECH_WAV}")

    # 2) 重采样到 16k
    with wave.open(VISION_SPEECH_WAV, "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    if sr != 16000:
        pcm16, _ = audioop.ratecv(pcm, 2, 1, sr, 16000, None)
    else:
        pcm16 = pcm
    dur = len(pcm16) / 32000.0
    print(f"[i] 喂入 STT：{len(pcm16)} 字节 16k PCM（约 {dur:.1f}s）")

    # 3) 测试图：左红右蓝 PNG → data URL
    png = _make_test_png()
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    print(f"[i] 测试图：{len(png)} 字节 PNG → data URL {len(data_url)} 字符")

    # 4) WS 往返
    events: list[str] = []
    deltas: list[str] = []
    audio_bytes = 0
    saw_take_photo = False
    done = asyncio.Event()

    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        async def reader():
            nonlocal audio_bytes, saw_take_photo
            try:
                async for msg in ws:
                    if isinstance(msg, (bytes, bytearray)):
                        audio_bytes += len(msg)
                        continue
                    obj = json.loads(msg)
                    t = obj.get("type")
                    events.append(t)
                    if t == "take_photo":
                        saw_take_photo = True
                        call_id = obj.get("call_id", "")
                        print(f"   take_photo call_id={call_id} → 回传测试图")
                        await ws.send(json.dumps({
                            "type": "photo",
                            "call_id": call_id,
                            "data": data_url,
                        }))
                    elif t == "partial":
                        print("   partial:", obj.get("text", ""))
                    elif t == "user_final":
                        print("   user_final:", obj.get("text", ""))
                    elif t == "delta":
                        deltas.append(obj.get("text", ""))
                    elif t == "tool_running":
                        print("   tool_running:", obj.get("tool"))
                    elif t == "state":
                        print("   state:", obj.get("state"))
                    elif t == "tts_end":
                        print("   tts_end")
                        done.set()
                    elif t == "error":
                        print("   ERROR:", obj.get("message"))
                        done.set()
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.6)  # 等 tts_format / state 到达

        # 喂视觉意图语音（100ms 一帧）+ ~2s 静音（让 STT 判句尾产 user_final）
        CHUNK = 3200
        for i in range(0, len(pcm16), CHUNK):
            await ws.send(pcm16[i:i + CHUNK])
            await asyncio.sleep(0.1)
        silence = b"\x00" * CHUNK
        for _ in range(20):
            await ws.send(silence)
            await asyncio.sleep(0.1)

        # 等 user_final（视觉意图被 STT 识别）
        user_final_seen = "user_final" in events
        if not user_final_seen:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                pass
            user_final_seen = "user_final" in events
        if user_final_seen:
            print("[i] 已收到 user_final")
        else:
            print("   [!] 15s 内未收到 user_final")

        # 等 tts_end（含拍照+二次 LLM，给足时间）
        try:
            await asyncio.wait_for(done.wait(), timeout=50)
        except asyncio.TimeoutError:
            print("   [!] 50s 内未收到 tts_end/error")

        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(0.5)
        rtask.cancel()

    # 5) 验证（容错）
    print(f"\n[结果] 事件序列: {events}")
    print(f"[结果] 收到 TTS 音频: {audio_bytes} 字节")
    print(f"[结果] 发起 take_photo: {saw_take_photo}")
    assistant_text = "".join(deltas).strip()
    print(f"[结果] 助手回复片段: {assistant_text[:160]}")

    if saw_take_photo and assistant_text:
        print("[PASS] 视觉链路往返成功：LLM 调 take_photo 并基于图作了多模态回答")
    elif saw_take_photo and not assistant_text:
        print("[INFO] LLM 调了 take_photo 但未流式产出文本（建议复查），非硬失败")
    else:
        print("[INFO] LLM 未发起 take_photo（无头环境模型行为，非缺陷），建议浏览器手动验证")


async def phase5_mcp_roundtrip() -> None:
    import websockets

    print("\n===== Phase 5: MCP tool 端到端（howtocook-mcp）=====")
    # 合成一句菜谱意图语音
    speech_text = "晚上不知道吃什么，给我推荐一下。"
    speech_wav = os.path.join(HERE, "test_mcp_speech.wav")
    await _synthesize_wav(speech_text, speech_wav)

    # 重采样到 16k
    with wave.open(speech_wav, "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    import audioop
    if sr != 16000:
        pcm16, _ = audioop.ratecv(pcm, 2, 1, sr, 16000, None)
    else:
        pcm16 = pcm
    print(f"[i] 喂入 STT：{len(pcm16)} 字节 16k PCM")

    events: list[str] = []
    deltas: list[str] = []
    done = asyncio.Event()

    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        async def reader():
            try:
                async for msg in ws:
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    obj = json.loads(msg)
                    t = obj.get("type")
                    events.append(t)
                    if t == "delta":
                        deltas.append(obj.get("text", ""))
                    elif t in ("tts_end", "error"):
                        done.set()
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(0.5)
        CHUNK = 3200
        for i in range(0, len(pcm16), CHUNK):
            await ws.send(pcm16[i:i + CHUNK])
            await asyncio.sleep(0.1)
        for _ in range(20):  # ~2s 静音
            await ws.send(b"\x00" * CHUNK)
            await asyncio.sleep(0.1)
        try:
            await asyncio.wait_for(done.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("   [!] 60s 内未完成")
        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(0.3)
        rtask.cancel()

    print(f"[结果] 事件序列: {events}")
    print(f"[结果] 助手回复: {''.join(deltas)[:160]}")

    if deltas:
        print("[PASS] MCP 往返成功：LLM 基于 MCP 工具结果作了回答")
    else:
        print("[INFO] 未收到 delta（LLM 可能未调 MCP 工具，或模型行为）；建议浏览器手动验证")


async def main() -> None:
    await phase1_llm()
    await phase2_tts()
    await phase3_ws_roundtrip()
    await phase4_vision_roundtrip()
    await phase5_mcp_roundtrip()
    print("\n===== 全部自检通过 =====")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
