"""Prompt assembly types: PromptPack (in-flight) and PromptMeta (persisted).

Per docs/rebuild/02-TDD.md §3.4 + 09-LLM-Strategy.md §4.

Key invariants:
- PromptMeta uses `system_excerpt` (≤ ~500 chars), NOT the full system text,
  to keep messages.meta_json from exploding (lesson 3.4).
- Composer outputs structured `PromptPack.sections`; L0 checks index by key
  rather than string-matching titles (lesson 3.5).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memory import RoutedMemoryItem, SnapshotStats
from app.domain.route import MemoryRoute


class SectionKey(StrEnum):
    """Stable keys for prompt segments (doc 09 §3.1)."""

    BASE_PERSONA = "base_persona"
    TIME_CONTEXT = "time_context"
    PROFILE = "profile"
    RELATIONSHIPS = "relationships"
    EVENTS = "events"
    EXPLICIT_MEMORIES = "explicit_memories"
    BACKGROUND_MEMORIES = "background_memories"
    TURN_RULES = "turn_rules"
    INTENT_GUIDE = "intent_guide"
    PERSONALITY_CONTRACT = "personality_contract"
    HARD_RULES = "hard_rules"


class LLMRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    enable_thinking: bool = False


class ContractEnforcement(BaseModel):
    """Records whether the post-LLM contract guard had to step in (doc 04 §9.4).

    `truncated=True` means the model exceeded its persona word-budget;
    `removed_question=True` means an introvert reply was trailing-question stripped.
    Both fed into eval to measure raw model adherence (D3).
    """

    model_config = ConfigDict(frozen=False)

    truncated: bool = False
    removed_question: bool = False
    raw_char_count: int | None = None
    final_char_count: int | None = None


class MemoryRef(BaseModel):
    """Compact reference to a memory item used in the activated list."""

    model_config = ConfigDict(frozen=True)

    source: str  # profile / event / episodic / relationship
    ref_id: str
    excerpt: str = ""  # ≤ 80 chars


class PromptMeta(BaseModel):
    """Persisted with each assistant message. The basis for real-chat eval."""

    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    composed_at: datetime
    model: str

    route: MemoryRoute
    activated: list[MemoryRef] = Field(default_factory=list)
    snapshot_stats: SnapshotStats = Field(default_factory=SnapshotStats)
    section_keys: list[SectionKey] = Field(default_factory=list)

    # NOTE: keep this short to avoid messages.meta_json bloat (lesson 3.4).
    # The full system text can be reconstructed by replaying composer over
    # snapshot_stats + route in a debug endpoint.
    system_excerpt: str = ""

    llm_request: LLMRequestRef
    contract_enforced: ContractEnforcement = Field(default_factory=ContractEnforcement)
    estimated_tokens: int = 0


class PromptPack(BaseModel):
    """In-flight bundle returned by composer. Not persisted as-is —
    only `meta` is serialized to messages.meta_json.
    """

    model_config = ConfigDict(frozen=False)

    system: str
    sections: dict[SectionKey, str | None] = Field(default_factory=dict)
    meta: PromptMeta

    # The actual list of memory items that the composer chose to render
    # in the explicit pool. Used by chat orchestrator to log `activated`.
    activated_items: list[RoutedMemoryItem] = Field(default_factory=list)
