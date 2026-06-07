"""Online correction pipeline domain types.

Per docs/rebuild/06-Subsystem-Correction.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CorrectionAction = Literal["deprecate", "update", "audit_only"]
CorrectionSource = Literal["episodic", "event", "profile", "entity"]


class CorrectionTarget(BaseModel):
    """One target extracted from the user's correction utterance."""

    model_config = ConfigDict(frozen=True)

    ref: str  # the entity being negated, e.g. "岳西"
    verb: str  # "不是" / "记错"
    correct: str | None = None  # e.g. "怀宁" — None when user only negates


class CorrectionCandidate(BaseModel):
    """A memory candidate found by searching across the four layers."""

    model_config = ConfigDict(frozen=True)

    source: CorrectionSource
    ref_id: str
    text: str


class CorrectionJudgement(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: CorrectionAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    new_text: str | None = None  # required when action == "update"


class MemoryDeprecation(BaseModel):
    """Persisted into `memory_deprecations` (doc 08 §2.4)."""

    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    id: int | None = None  # autoincrement on the DB side
    user_id: str
    source: CorrectionSource
    ref_id: str
    original_text: str | None = None
    new_text: str | None = None
    reason: str = ""
    correction_conversation_id: str | None = None
    correction_turn_id: str | None = None
    llm_confidence: float = 0.0
    action: CorrectionAction = "audit_only"
    deprecated_at: datetime | None = None
    restored_at: datetime | None = None


class BannedEntity(BaseModel):
    """Persisted into `banned_entities` (doc 06 §6.4: cleaned + deduped)."""

    model_config = ConfigDict(frozen=False)

    user_id: str
    entity: str
    reason: str = ""
    created_at: datetime | None = None

    @field_validator("entity")
    @classmethod
    def _clean(cls, v: str) -> str:
        # Doc 06 §6.4: strip whitespace, length 1..8, otherwise reject.
        v = v.strip()
        if not v:
            raise ValueError("BannedEntity.entity must not be empty after strip")
        if len(v) > 8:
            raise ValueError("BannedEntity.entity must be ≤ 8 chars (lesson 5.2)")
        return v
