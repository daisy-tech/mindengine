"""LLM model id constants — single source of truth.

Per docs/rebuild/09-LLM-Strategy.md §1.1: all model ids live here as a
constant table; business code and other docs reference these by name,
never by literal string (lesson 2.4: "intent string scattered" applies
equally to model ids).
"""

from __future__ import annotations

from enum import StrEnum


class LLMRole(StrEnum):
    """Roles routed by LLMRouter.for_role(...)."""

    CHAT = "chat"
    INTENT = "intent"
    EXTRACT = "extract"
    CORRECTION = "correction"
    JUDGE = "judge"
    EMBEDDING = "embedding"


# Default model id per role.
# Naming convention: lowercase-hyphenated; verify against the vendor console
# (DashScope / OpenAI / Anthropic) before bumping versions.
DEFAULT_MODELS: dict[LLMRole, str] = {
    LLMRole.CHAT: "qwen3.7-max",
    LLMRole.INTENT: "qwen3.7-plus",
    LLMRole.EXTRACT: "qwen3.7-plus",
    LLMRole.CORRECTION: "qwen3.7-plus",
    LLMRole.JUDGE: "qwen3.7-plus",
    LLMRole.EMBEDDING: "text-embedding-v3",
}


# Cost table for trace aggregation (USD per 1k tokens).
# Numbers are placeholders until verified against vendor pricing.
COST_TABLE: dict[str, tuple[float, float]] = {
    # model_id -> (input_per_1k, output_per_1k)
    "qwen3.7-max": (0.012, 0.048),
    "qwen3.7-plus": (0.004, 0.012),
    "text-embedding-v3": (0.0007, 0.0),
}


# Embedding dim must match the pgvector column definition (see infra/db/models.py).
EMBEDDING_DIM = 1024
