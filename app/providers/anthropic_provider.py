from __future__ import annotations

from anthropic import AsyncAnthropic

from .base import ChatMessage


class AnthropicProvider:
    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    async def chat(self, messages: list[ChatMessage], model: str) -> str:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        convo = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in ("user", "assistant")
        ]
        kwargs = {"model": model, "max_tokens": 4096, "messages": convo}
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        resp = await self.client.messages.create(**kwargs)
        chunks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(chunks)
