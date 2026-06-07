"""DashScope embedding client (OpenAI-compatible).

Per docs/rebuild/09-LLM-Strategy.md §2.2 + 05-Subsystem-Memory-Layers §5.2.

Implements ``services.protocols.EmbeddingClient`` against DashScope's
OpenAI-compatible ``/v1/embeddings`` endpoint. The model is configurable
(default ``text-embedding-v3``, dim=1024 — must match the pgvector
column dimension declared in ``app.infra.db.models.EpisodicMemoryRow``).

Errors are mapped to ``app.infra.llm.exceptions`` so the worker layer
can retry uniformly with the chat client.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from openai import APIError as _OpenAIAPIError
from openai import AsyncOpenAI

from app.domain.llm import EMBEDDING_DIM
from app.infra.llm.exceptions import (
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
)


@dataclass
class DashScopeEmbedder:
    """One-model embedding client.

    DashScope counts each input string as one chunk; small batches are
    fine but the API caps at 25 inputs/request — we chunk transparently.
    """

    client: AsyncOpenAI
    model: str = "text-embedding-v3"
    dim: int = EMBEDDING_DIM
    request_timeout_s: float = 30.0
    batch_size: int = 25

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("DashScopeEmbedder.embed: text must not be empty")
        vecs = await self._call([text])
        return vecs[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # Filter blank strings to fail fast — silently embedding empty
        # text wastes tokens and yields garbage neighbors later.
        cleaned = [t for t in texts if t and t.strip()]
        if len(cleaned) != len(texts):
            raise ValueError("DashScopeEmbedder.embed_batch: empty text in batch")

        out: list[list[float]] = []
        for i in range(0, len(cleaned), self.batch_size):
            chunk = cleaned[i : i + self.batch_size]
            out.extend(await self._call(chunk))
        return out

    async def _call(self, inputs: list[str]) -> list[list[float]]:
        try:
            resp = await self.client.embeddings.create(
                model=self.model,
                input=inputs,
                timeout=self.request_timeout_s,
            )
        except Exception as e:
            raise self._wrap(e) from e

        # OpenAI-compatible: resp.data preserves request order via .index.
        ordered = sorted(resp.data, key=lambda d: d.index)
        vecs = [list(d.embedding) for d in ordered]
        for v in vecs:
            if len(v) != self.dim:
                raise ValueError(
                    f"Embedder returned dim={len(v)}, expected {self.dim} "
                    f"(model={self.model!r})"
                )
        return vecs

    @staticmethod
    def _wrap(e: Exception) -> Exception:
        if isinstance(e, _OpenAIAPIError):
            status = getattr(e, "status_code", None)
            if status == 429:
                return LLMRateLimitError(str(e))
            if status in (408, 504):
                return LLMTimeoutError(str(e))
        if isinstance(e, asyncio.TimeoutError | TimeoutError):
            return LLMTimeoutError(str(e))
        return LLMNetworkError(str(e))
