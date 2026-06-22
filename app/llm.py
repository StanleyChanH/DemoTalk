"""LLM：封装阿里云百炼 qwen3.7-plus 流式对话（OpenAI 兼容接口）。

关键点：qwen3.7-plus 默认开启「思考模式」，会先吐一大段 reasoning_content 才给正文，
对语音助手是巨大延迟。必须通过 extra_body={"enable_thinking": False} 关闭。
正文在 chunk.choices[0].delta.content；思考链在 delta.reasoning_content（关闭后为空）。
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from . import config

log = logging.getLogger("demotalk.llm")


class LLMService:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.LLM_BASE_URL,
        )
        self._history: list[dict[str, str]] = [
            {"role": "system", "content": config.LLM_SYSTEM_PROMPT}
        ]

    async def astream(self, user_text: str) -> AsyncIterator[str]:
        """流式生成回答，逐段 yield 正文增量。"""
        self._history.append({"role": "user", "content": user_text})

        collected: list[str] = []
        try:
            stream = await self._client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=self._history,
                stream=True,
                temperature=config.LLM_TEMPERATURE,
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": False},
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) or ""
                if content:
                    collected.append(content)
                    yield content
        except Exception as e:
            log.exception("LLM 流式调用失败")
            # 失败时把一个可见错误交给下游（前端/日志）
            yield f"（模型调用失败：{e}）"

        # 记录助手回复并裁剪历史
        full = "".join(collected)
        if full:
            self._history.append({"role": "assistant", "content": full})
        self._trim_history()

    def _trim_history(self) -> None:
        """保留 system + 最近 N 轮（每轮 = user + assistant）。"""
        sys_msgs = [m for m in self._history if m["role"] == "system"]
        convo = [m for m in self._history if m["role"] != "system"]
        # 每轮两条
        keep_pairs = config.LLM_HISTORY_TURNS
        convo = convo[-keep_pairs * 2 :]
        self._history = sys_msgs + convo

    def reset(self) -> None:
        self._history = [{"role": "system", "content": config.LLM_SYSTEM_PROMPT}]
