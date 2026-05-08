from __future__ import annotations

import asyncio

from google import genai
from google.genai import types

from .base import ChatMessage


class GeminiProvider:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    async def chat(self, messages: list[ChatMessage], model: str) -> str:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))

        config = None
        if system_parts:
            config = types.GenerateContentConfig(system_instruction="\n\n".join(system_parts))

        def _call():
            return self.client.models.generate_content(
                model=model, contents=contents, config=config
            )

        resp = await asyncio.to_thread(_call)
        return resp.text or ""
