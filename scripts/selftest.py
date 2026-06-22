"""DemoTalk 真实全链路自检。

用法：
  1) 先启动服务：  uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  2) 另开终端运行： uv run python scripts/selftest.py

做了三件事：
  Phase 1 —— 直接调 LLM（qwen3.7-plus）流式，验证 Key/网络/思考关闭。
  Phase 2 —— 直接调 TTS（cosyvoice-v3-flash）流式，收集 PCM 写成 wav（供 Phase3 当麦克风输入）。
  Phase 3 —— 起一个 WebSocket 客户端，把 Phase2 的语音按 16k/100ms 喂给 /ws，
             期望收到：partial → user_final → delta(打字机) → TTS 二进制音频 → tts_end。
             一次打通 session.py 的 STT→LLM→TTS 编排。
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
TTS_TEXT = "你好，我是一个实时语音助手，很高兴和你对话。"


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
    from app import config
    from app.tts import TTSService

    print("\n===== Phase 2: TTS 真实流式调用 → 写 wav =====")
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
    tts.feed(TTS_TEXT)
    tts.finish()
    await asyncio.wait_for(done.wait(), timeout=40)
    await asyncio.sleep(0.3)  # 等尾部包到齐

    pcm = b"".join(chunks)
    assert len(pcm) > 2000, f"TTS PCM 过少：{len(pcm)} 字节"
    with wave.open(SPEECH_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.TTS_SAMPLE_RATE)
        w.writeframes(pcm)

    delay = None
    try:
        delay = tts._synth.get_first_package_delay()  # type: ignore[attr-defined]
    except Exception:
        pass
    print(f"[PASS] TTS(cosyvoice-v3-flash) 收到 {len(pcm)} 字节，首包延迟={delay}ms")
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


async def main() -> None:
    await phase1_llm()
    await phase2_tts()
    await phase3_ws_roundtrip()
    print("\n===== 全部自检通过 =====")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
