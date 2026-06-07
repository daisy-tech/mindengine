"""In-memory LLM client for tests.

Service-layer tests must not touch the network (doc 02 §1.2). This client
implements the LLMClient Protocol with deterministic, scriptable replies.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMInvalidJSONError


@dataclass
class MockTurn:
    """One scripted reply.

    - text: full final text, also used to slice into stream chunks
    - chunks: optional pre-sliced chunks (overrides default char-by-char streaming)
    - json_payload: dict returned by complete_json
    - raises: an exception to raise instead of replying
    """

    text: str | None = None
    chunks: list[str] | None = None
    json_payload: dict[str, Any] | None = None
    raises: Exception | None = None


@dataclass
class MockLLMClient:
    """Implements `LLMClient` Protocol for tests."""

    role: LLMRole = LLMRole.CHAT
    model: str = "mock-model"
    turns: deque[MockTurn] = field(default_factory=deque)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def queue(self, *turns: MockTurn) -> MockLLMClient:
        self.turns.extend(turns)
        return self

    def queue_text(self, *texts: str) -> MockLLMClient:
        for t in texts:
            self.turns.append(MockTurn(text=t))
        return self

    def queue_json(self, *payloads: dict[str, Any]) -> MockLLMClient:
        for p in payloads:
            self.turns.append(MockTurn(json_payload=p))
        return self

    # ------------------------------------------------------------------ helpers

    def _next(self) -> MockTurn:
        if not self.turns:
            raise AssertionError("MockLLMClient: no scripted turns left to consume")
        return self.turns.popleft()

    def _record(self, op: str, system: str, messages: Sequence[dict[str, str]], **kw: Any) -> None:
        self.calls.append({"op": op, "system": system, "messages": list(messages), **kw})

    # ------------------------------------------------------------------ Protocol

    async def complete(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        self._record("complete", system, messages, temperature=temperature, max_tokens=max_tokens)
        turn = self._next()
        if turn.raises is not None:
            raise turn.raises
        if turn.text is None:
            raise AssertionError("MockTurn for complete() must set .text")
        return turn.text

    async def stream(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self._record("stream", system, messages, temperature=temperature, max_tokens=max_tokens)
        turn = self._next()
        if turn.raises is not None:
            raise turn.raises
        chunks: list[str]
        if turn.chunks is not None:
            chunks = list(turn.chunks)
        elif turn.text is not None:
            # Default: char-by-char so tests can observe partials.
            chunks = list(turn.text)
        else:
            raise AssertionError("MockTurn for stream() must set .text or .chunks")
        return _aiter(chunks)

    async def complete_json(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        schema: type[Any],
        temperature: float = 0.0,
    ) -> Any:
        self._record(
            "complete_json", system, messages, temperature=temperature, schema=schema.__name__
        )
        turn = self._next()
        if turn.raises is not None:
            raise turn.raises
        if turn.json_payload is None:
            raise AssertionError("MockTurn for complete_json() must set .json_payload")
        try:
            return schema(**turn.json_payload)
        except Exception as e:  # pragma: no cover — defensive
            raise LLMInvalidJSONError(f"Mock payload not valid for {schema.__name__}: {e}") from e


async def _aiter(chunks: Sequence[str]) -> AsyncIterator[str]:
    for c in chunks:
        yield c


@dataclass
class MockEmbeddingClient:
    """Deterministic non-trivial embeddings for tests.

    The vector is just a hashed projection — the only invariants tested
    are dim and stability (same text → same vector).
    """

    model: str = "mock-embedding"
    dim: int = 1024

    async def embed(self, text: str) -> list[float]:
        rng_seed = hash(text) & 0xFFFFFFFF
        # Tiny LCG so we don't import random module just for tests.
        a, c, m = 1664525, 1013904223, 2**32
        seed = rng_seed
        out: list[float] = []
        for _ in range(self.dim):
            seed = (a * seed + c) % m
            out.append((seed / m) * 2.0 - 1.0)
        return out

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


def parse_json_text(text: str, schema: type[Any]) -> Any:
    """Helper for tests / structured handlers: strip fences then validate."""
    from app.infra.llm.utils import strip_json_fences

    stripped = strip_json_fences(text)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise LLMInvalidJSONError(f"Cannot parse JSON: {stripped!r}") from e
    return schema(**data)
