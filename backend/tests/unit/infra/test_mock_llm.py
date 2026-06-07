"""Tests for the in-memory MockLLMClient — these guard the test harness itself."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.domain.route import ClassifyResult, Intent
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import (
    MockEmbeddingClient,
    MockLLMClient,
    MockTurn,
    parse_json_text,
)


@pytest.mark.asyncio
async def test_complete_returns_scripted_text() -> None:
    client = MockLLMClient(role=LLMRole.CHAT).queue_text("你好")
    out = await client.complete("sys", [{"role": "user", "content": "嗨"}])
    assert out == "你好"
    assert client.calls[0]["op"] == "complete"


@pytest.mark.asyncio
async def test_stream_yields_chars_by_default() -> None:
    client = MockLLMClient().queue_text("abc")
    chunks: list[str] = []
    stream = await client.stream("sys", [{"role": "user", "content": "x"}])
    async for c in stream:
        chunks.append(c)
    assert chunks == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_stream_uses_explicit_chunks() -> None:
    client = MockLLMClient().queue(MockTurn(chunks=["你好", "，世界"]))
    chunks: list[str] = []
    stream = await client.stream("sys", [{"role": "user", "content": "x"}])
    async for c in stream:
        chunks.append(c)
    assert chunks == ["你好", "，世界"]


@pytest.mark.asyncio
async def test_complete_json_parses_into_schema() -> None:
    client = MockLLMClient(role=LLMRole.INTENT).queue_json(
        {"intent": "casual", "confidence": 0.9, "reason": "hi"}
    )
    result = await client.complete_json(
        "sys", [{"role": "user", "content": "你好"}], schema=ClassifyResult
    )
    assert isinstance(result, ClassifyResult)
    assert result.intent == Intent.CASUAL


@pytest.mark.asyncio
async def test_raises_when_scripted() -> None:
    client = MockLLMClient().queue(MockTurn(raises=LLMRateLimitError("429")))
    with pytest.raises(LLMRateLimitError):
        await client.complete("sys", [])


@pytest.mark.asyncio
async def test_runs_out_of_turns_is_a_loud_assertion() -> None:
    client = MockLLMClient()
    with pytest.raises(AssertionError):
        await client.complete("sys", [])


@pytest.mark.asyncio
async def test_mock_embedding_is_deterministic_and_correct_dim() -> None:
    em = MockEmbeddingClient()
    a = await em.embed("hello")
    b = await em.embed("hello")
    c = await em.embed("world")
    assert len(a) == em.dim == 1024
    assert a == b
    assert a != c


def test_parse_json_text_strips_fences() -> None:
    raw = '```json\n{"intent": "casual", "confidence": 0.9, "reason": "ok"}\n```'
    result = parse_json_text(raw, ClassifyResult)
    assert result.intent == Intent.CASUAL
