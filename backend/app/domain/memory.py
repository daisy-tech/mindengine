"""Four-layer memory domain types.

Per docs/rebuild/05-Subsystem-Memory-Layers.md and 08-Data-Model.md.
Each persistence type carries `schema_version` to support migration
(lesson 4.1: profile field type drift).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ───────────────────────────────────────────────────────── profile ──


class BasicInfo(BaseModel):
    name: str | None = None
    birth_year: int | None = None
    gender: Literal["male", "female", "other"] | None = None
    location: str | None = None


class Occupation(BaseModel):
    title: str | None = None
    industry: str | None = None
    level: str | None = None


class FamilyHint(BaseModel):
    structure: str | None = None  # free-form short text
    notes: list[str] = Field(default_factory=list)


class UserCorrection(BaseModel):
    """Audit row appended each time the user corrects profile (doc 05 §3.3)."""

    field_path: str
    old_value: str | None = None
    new_value: str | None = None
    happened_at: datetime


class Profile(BaseModel):
    """One row per user. Stored as JSON in `profiles.data_json`."""

    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    user_id: str
    basic: BasicInfo = Field(default_factory=BasicInfo)
    interests: list[str] = Field(default_factory=list)
    occupation: Occupation = Field(default_factory=Occupation)
    family_structure: FamilyHint = Field(default_factory=FamilyHint)
    extra: dict[str, str] = Field(default_factory=dict)
    user_corrections: list[UserCorrection] = Field(default_factory=list)
    updated_at: datetime | None = None


# Field merge policy (doc 05 §3.3): explicit lists prevent type-drift bugs.
PROFILE_ACCUMULATIVE_FIELDS: frozenset[str] = frozenset(
    {"interests", "user_corrections", "family_structure.notes"}
)
PROFILE_OVERRIDE_FIELDS: frozenset[str] = frozenset(
    {
        "basic.name",
        "basic.birth_year",
        "basic.gender",
        "basic.location",
        "occupation.title",
        "occupation.industry",
        "occupation.level",
        "family_structure.structure",
    }
)


# ─────────────────────────────────────────────────────── event ──


EventType = Literal["experience", "plan", "milestone", "challenge", "reflection"]
EventStatus = Literal["active", "deprecated"]


class Event(BaseModel):
    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    id: str
    user_id: str
    type: EventType
    title: str = Field(max_length=200)
    content: str
    occurred_at: datetime | None = None
    status: EventStatus = "active"
    source_message_id: str | None = None
    created_at: datetime | None = None


# ─────────────────────────────────────────────────────── episodic ──


EpisodicStatus = Literal["active", "deprecated"]


class EpisodicMemory(BaseModel):
    """Stored in Postgres `episodic_memories` with pgvector embedding column.

    The embedding itself is not part of the domain DTO (it lives in infra);
    domain only needs id/text/status for service-layer logic.
    """

    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    id: str
    user_id: str
    text: str
    status: EpisodicStatus = "active"
    source_message_id: str | None = None
    source: str | None = None
    created_at: datetime | None = None


class EpisodicHit(BaseModel):
    """Search result returned by EpisodicRepository.search."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    score: float = Field(ge=0.0, le=1.0)


# ─────────────────────────────────────────────────────── relationship ──


class Relationship(BaseModel):
    model_config = ConfigDict(frozen=False)

    schema_version: int = 1
    id: str
    user_id: str
    name: str = Field(max_length=100)
    role: str = Field(max_length=50)  # 妻子 / 儿子 / 同事 ...
    attributes: dict[str, str] = Field(default_factory=dict)
    via: str | None = None  # FK to relationships.id; never == self.id
    status: EventStatus = "active"
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _no_self_loop(self) -> Relationship:
        # Lesson 4.3: relationship via=self crashes the social graph UI.
        if self.via is not None and self.via == self.id:
            raise ValueError("Relationship.via must not point to self")
        return self


# ─────────────────────────────────────────────────────── context ──


class MemoryContext(BaseModel):
    """Result of services/memory_context/loader.load(user_id, route).

    Pools are pre-tagged with usage. Composer renders explicit pools into
    the system prompt; background_only stays as ambient knowledge.
    """

    model_config = ConfigDict(frozen=False)

    stable_profile: list[RoutedMemoryItem] = Field(default_factory=list)
    relevant_relationships: list[RoutedMemoryItem] = Field(default_factory=list)
    relevant_events: list[RoutedMemoryItem] = Field(default_factory=list)
    relevant_memories: list[RoutedMemoryItem] = Field(default_factory=list)
    background_only: list[RoutedMemoryItem] = Field(default_factory=list)
    snapshot_stats: SnapshotStats = Field(default_factory=lambda: SnapshotStats())


class RoutedMemoryItem(BaseModel):
    model_config = ConfigDict(frozen=False)

    source: Literal["profile", "event", "episodic", "relationship"]
    ref_id: str
    text: str
    usage: str  # MemoryUsage value; kept as str to break import cycle
    score: float | None = None


class SnapshotStats(BaseModel):
    """Pool size summary persisted in PromptMeta so eval can run without
    re-loading memory (doc 02 §3.4)."""

    model_config = ConfigDict(frozen=False)

    profile_total: int = 0
    event_total: int = 0
    episodic_total: int = 0
    relationship_total: int = 0
    banned_entities: list[str] = Field(default_factory=list)


MemoryContext.model_rebuild()
