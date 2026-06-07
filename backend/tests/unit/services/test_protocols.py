"""Tests that the in-memory MockLLMClient actually satisfies the
LLMClient Protocol shape, so service-layer tests can swap it in.
"""

from __future__ import annotations

from typing import get_type_hints

from app.domain.llm import LLMRole
from app.infra.llm.mock_client import MockEmbeddingClient, MockLLMClient
from app.services.protocols import EmbeddingClient, LLMClient


def test_mock_llm_satisfies_protocol_runtime() -> None:
    client = MockLLMClient(role=LLMRole.CHAT, model="m")
    assert isinstance(client, LLMClient)


def test_mock_embedding_satisfies_protocol_runtime() -> None:
    em = MockEmbeddingClient()
    assert isinstance(em, EmbeddingClient)


def test_mock_llm_has_required_attrs() -> None:
    # Quick guard so signature drift in MockLLMClient doesn't sneak in.
    client = MockLLMClient()
    assert hasattr(client, "role")
    assert hasattr(client, "model")
    for method in ("complete", "stream", "complete_json"):
        assert callable(getattr(client, method))


def test_protocol_method_names_stable() -> None:
    # If you rename one of these, every service test needs to be reviewed.
    # Note: Protocols expose methods through dir(); class-level data attrs
    # (role, model) live in __annotations__ instead.
    members = {m for m in dir(LLMClient) if not m.startswith("_")}
    assert {"complete", "stream", "complete_json"} <= members
    annotations = LLMClient.__annotations__
    assert "role" in annotations
    assert "model" in annotations


def test_get_type_hints_works() -> None:
    # Smoke-check that the Protocol has resolvable annotations
    # (catches accidental forward-ref typos).
    hints = get_type_hints(LLMClient.complete)
    assert "system" in hints
    assert "messages" in hints
