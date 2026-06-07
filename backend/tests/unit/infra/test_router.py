"""Tests for LLMRouter."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.infra.llm.mock_client import MockLLMClient
from app.infra.llm.router import LLMRouter


def test_register_and_lookup() -> None:
    router = LLMRouter()
    chat = MockLLMClient(role=LLMRole.CHAT, model="m1")
    intent = MockLLMClient(role=LLMRole.INTENT, model="m2")
    router.register(LLMRole.CHAT, chat)
    router.register(LLMRole.INTENT, intent)

    assert router.has(LLMRole.CHAT)
    assert router.has(LLMRole.INTENT)
    assert router.for_role(LLMRole.CHAT) is chat
    assert router.for_role(LLMRole.INTENT) is intent


def test_unknown_role_raises_with_helpful_message() -> None:
    router = LLMRouter()
    router.register(LLMRole.CHAT, MockLLMClient())
    with pytest.raises(RuntimeError, match="no client registered for role"):
        router.for_role(LLMRole.JUDGE)
