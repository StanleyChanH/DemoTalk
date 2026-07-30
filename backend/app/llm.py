"""LLM：封装阿里云百炼 qwen3.7-plus 流式对话（OpenAI 兼容接口）。

- astream_once(): 单次流式，yield 事件（text / done）。done 携带 tool_calls。
- add_user/add_tool(): 维护多模态 + tool 消息历史。
- astream(): 旧的纯文本流式接口，保留供 selftest Phase 1。

qwen3.7-plus 默认开思考模式，必须 extra_body={"enable_thinking": False} 关闭。
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
        self._history: list[dict] = [
            {"role": "system", "content": config.LLM_SYSTEM_PROMPT}
        ]

    # ---- 历史管理 ----
    def messages(self) -> list[dict]:
        return self._history

    def add_user(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})

    def add_tool(self, call_id: str, content: list[dict]) -> None:
        self._history.append({"role": "tool", "tool_call_id": call_id, "content": content})

    def reset(self) -> None:
        self._history = [{"role": "system", "content": config.LLM_SYSTEM_PROMPT}]

    # ---- 单次流式（带 tool 检测）----
    async def astream_once(self, tools: list[dict] | None = None) -> AsyncIterator[dict]:
        """基于当前 _history 做一次流式调用。

        yield {"type":"text","text":str} 文本增量，
        最后 yield {"type":"done","tool_calls":list,"finish_reason":str}。
        并把 assistant 消息（含 tool_calls）追加到 _history。
        """
        texts: list[str] = []
        tc_map: dict[int, dict] = {}
        finish: str | None = None
        try:
            stream = await self._client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=self._history,
                stream=True,
                tools=tools,
                temperature=config.LLM_TEMPERATURE,
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": False},
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                content = getattr(delta, "content", None) or ""
                if content:
                    texts.append(content)
                    yield {"type": "text", "text": content}
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        i = tc.index
                        slot = tc_map.setdefault(i, {"id": "", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
                if getattr(choice, "finish_reason", None):
                    finish = choice.finish_reason
        except Exception as e:
            log.exception("LLM 流式调用失败")
            yield {"type": "text", "text": f"（模型调用失败：{e}）"}
            yield {"type": "done", "tool_calls": [], "finish_reason": "error"}
            return

        tool_calls = list(tc_map.values()) if finish == "tool_calls" else []
        assistant_msg: dict = {"role": "assistant", "content": "".join(texts)}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self._history.append(assistant_msg)
        self._trim_history()
        yield {"type": "done", "tool_calls": tool_calls, "finish_reason": finish or "stop"}

    def _trim_history(self) -> None:
        """保留 system + 最近 N 轮（user+assistant）。tool 消息随其 assistant 保留。"""
        sys_msgs = [m for m in self._history if m["role"] == "system"]
        convo = [m for m in self._history if m["role"] != "system"]
        keep = config.LLM_HISTORY_TURNS * 2
        self._history = sys_msgs + convo[-keep:]

    # ---- 旧接口（selftest Phase 1 用）----
    async def astream(self, user_text: str) -> AsyncIterator[str]:
        self.add_user(user_text)
        async for event in self.astream_once(tools=None):
            if event["type"] == "text":
                yield event["text"]
