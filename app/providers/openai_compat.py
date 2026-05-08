from __future__ import annotations

from openai import AsyncOpenAI

from .base import ChatMessage


class OpenAICompatProvider:
    def __init__(self, api_key: str, base_url: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    async def chat(self, messages: list[ChatMessage], model: str) -> str:
        resp = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        )
        return resp.choices[0].message.content or ""
