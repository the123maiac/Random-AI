from __future__ import annotations

import struct
from typing import Iterable

import numpy as np
from openai import AsyncOpenAI

DEFAULT_MODEL = "nvidia/nv-embedqa-e5-v5"  # NIM default; override per-key
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class Embedder:
    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)
        self.model = model or DEFAULT_MODEL

    async def embed(self, texts: Iterable[str]) -> list[np.ndarray]:
        items = [t for t in texts if t]
        if not items:
            return []
        resp = await self.client.embeddings.create(
            model=self.model,
            input=items,
            extra_body={"input_type": "passage"},
        )
        return [np.asarray(d.embedding, dtype=np.float32) for d in resp.data]

    async def embed_query(self, text: str) -> np.ndarray:
        resp = await self.client.embeddings.create(
            model=self.model,
            input=[text],
            extra_body={"input_type": "query"},
        )
        return np.asarray(resp.data[0].embedding, dtype=np.float32)


def pack(vec: np.ndarray) -> bytes:
    arr = vec.astype(np.float32)
    return struct.pack(f"{len(arr)}f", *arr.tolist())


def unpack(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.asarray(struct.unpack(f"{n}f", blob), dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def top_k(query: np.ndarray, items: list[tuple[int, np.ndarray]], k: int = 8) -> list[tuple[int, float]]:
    scored = [(i, cosine(query, v)) for i, v in items]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
