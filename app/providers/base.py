from __future__ import annotations

from typing import Protocol, TypedDict


class ChatMessage(TypedDict):
    role: str
    content: str


class Provider(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> str: ...


def get_provider(provider_type: str, api_key: str, base_url: str | None = None) -> Provider:
    if provider_type == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(api_key=api_key, base_url=base_url)
    if provider_type == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key)
    if provider_type == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(api_key=api_key)
    raise ValueError(f"Unknown provider type: {provider_type}")
