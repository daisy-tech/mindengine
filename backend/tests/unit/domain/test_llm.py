"""Tests for domain.llm — model id constants are the single source of truth."""

from __future__ import annotations

from app.domain.llm import (
    COST_TABLE,
    DEFAULT_MODELS,
    EMBEDDING_DIM,
    LLMRole,
)


def test_six_roles_defined() -> None:
    # CHAT / INTENT / EXTRACT / CORRECTION / JUDGE / EMBEDDING
    assert {r.value for r in LLMRole} == {
        "chat",
        "intent",
        "extract",
        "correction",
        "judge",
        "embedding",
    }


def test_every_role_has_default_model() -> None:
    for role in LLMRole:
        assert role in DEFAULT_MODELS
        assert isinstance(DEFAULT_MODELS[role], str)
        assert DEFAULT_MODELS[role]  # non-empty


def test_model_id_naming_lowercase() -> None:
    # D8 + lesson 2.4: ids are lowercase-hyphenated, no `Qwen3.7-plus` drift.
    for role, mid in DEFAULT_MODELS.items():
        assert mid == mid.lower(), f"{role} default '{mid}' must be lowercase"


def test_cost_table_keys_are_known_models() -> None:
    for mid in COST_TABLE:
        # cost_table can list extras (e.g., embedding model), but no typos
        assert mid == mid.lower()
    # at least the chat model must have a cost row
    assert DEFAULT_MODELS[LLMRole.CHAT] in COST_TABLE


def test_embedding_dim_matches_pgvector_column() -> None:
    # If you change this, also bump pgvector column dim in models.py and
    # write a migration. Guarded so they don't drift apart.
    assert EMBEDDING_DIM == 1024
