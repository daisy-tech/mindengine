"""Memory Router domain types.

Per docs/rebuild/03-Subsystem-Memory-Router.md §5: MemoryRoute is the
output of the 3-layer router (hard rules → small-model intent → policy).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    """The 9 intents (doc 03 §2). Hard rules + small-model classifier
    must produce one of these. Unknown / parse failure → CASUAL fallback.
    """

    CASUAL = "casual"
    SELF_SUMMARY = "self_summary"
    MEMORY_CHALLENGE = "memory_challenge"
    RELATIONSHIP_TOPIC = "relationship_topic"
    EMOTIONAL_SUPPORT = "emotional_support"
    PLAN_FOLLOWUP = "plan_followup"
    PREFERENCE_REQUEST = "preference_request"
    CORRECTION = "correction"
    KNOWLEDGE_TASK = "knowledge_task"


class IntentSource(StrEnum):
    HARD_RULE = "hard_rule"
    SMALL_MODEL = "small_model"
    FALLBACK = "fallback"
    CACHE = "cache"  # served from intent cache (D4)


class Personality(StrEnum):
    INTROVERT = "introvert"
    BALANCED = "balanced"
    EXTROVERT = "extrovert"


class MemoryDepth(StrEnum):
    MINIMAL = "minimal"
    SAFE_FOCUSED = "safe_focused"
    FOCUSED = "focused"
    WIDE = "wide"


class EventPolicy(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    BACKGROUND_PAIN_POINTS = "background_pain_points"


class MemoryUsage(StrEnum):
    """Tag attached to each piece of loaded memory (doc 05 §8.3)."""

    EXPLICIT_OK = "EXPLICIT_OK"
    BACKGROUND_ONLY = "BACKGROUND_ONLY"
    FOLLOW_UP_ONCE = "FOLLOW_UP_ONCE"
    AVOID_UNLESS_ASKED = "AVOID_UNLESS_ASKED"


# Subset of layers that may appear in MemoryRoute.load_layers.
LoadLayer = Literal[
    "profile_basic",
    "profile",
    "relationships",
    "events",
    "episodic",
]


class ClassifyResult(BaseModel):
    """Output of services/memory_router/intent_classifier.py."""

    model_config = ConfigDict(frozen=True)

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class MemoryRoute(BaseModel):
    """Routing decision for a single chat turn (doc 03 §5.1).

    Note: the full route is persisted as part of `PromptMeta.route` so
    real-chat eval can replay logic without re-running the LLM.
    """

    model_config = ConfigDict(frozen=False)

    schema_version: int = 1

    intent: Intent
    intent_source: IntentSource
    intent_confidence: float = Field(ge=0.0, le=1.0)

    personality: Personality
    memory_depth: MemoryDepth
    load_layers: list[LoadLayer] = Field(default_factory=list)

    sensitive_mode: bool = False
    max_explicit_memories: int = Field(ge=0, le=5, default=0)
    event_policy: EventPolicy = EventPolicy.NONE

    query: str = ""
    reasons: list[str] = Field(default_factory=list)


class RoutedMemory(BaseModel):
    """A single memory item plus its usage tag (doc 05 §8.3)."""

    model_config = ConfigDict(frozen=False)

    source: Literal["profile", "event", "episodic", "relationship"]
    ref_id: str
    text: str
    usage: MemoryUsage
    score: float | None = None
