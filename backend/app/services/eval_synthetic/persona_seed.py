"""Seed the eval-only persona's memory state.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.5: synthetic eval should
not touch real users' memory; instead, ``EVAL_USER_ID`` is reset to a
known baseline before each run.

The seeder is repo-Protocol-driven so unit tests can verify the planned
inserts without a real database. The API layer wires it to the live
repos behind the ``allow_destructive_dev`` flag.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.domain.memory import (
    BasicInfo,
    Event,
    Occupation,
    Profile,
    Relationship,
)
from app.services.protocols import (
    EpisodicRepository,
    EventRepository,
    ProfileRepository,
    RelationshipRepository,
)


@dataclass(frozen=True)
class SeedSummary:
    user_id: str
    profile_set: bool
    events_inserted: int
    episodic_inserted: int
    relationships_upserted: int


@dataclass
class PersonaSeed:
    """A baseline state for the eval persona.

    Defaults yield a recognizable persona ("张三 / 老家湖南怀化") that
    reproduces the demo script in docs/rebuild/11-Roadmap.md §"Demo
    Script". Tests can override every section.
    """

    profile: Profile | None = None
    events: list[Event] = field(default_factory=list)
    episodic: list[str] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)


def default_seed(user_id: str) -> PersonaSeed:
    """Return the canonical eval baseline for ``user_id``.

    The values mirror the Demo Script narrative so eval failures can be
    interpreted by reading docs/rebuild/11-Roadmap.md §"验收 Demo".
    """
    now = datetime.now(UTC)
    profile = Profile(
        user_id=user_id,
        basic=BasicInfo(name="张三", birth_year=1990, location="湖南怀化"),
        interests=["跑步", "阅读"],
        occupation=Occupation(title="后端工程师", industry="互联网"),
    )
    events = [
        Event(
            id=str(uuid4()),
            user_id=user_id,
            type="experience",
            title="搬到上海工作",
            content="2020 年因工作搬到上海。",
            occurred_at=now,
            source_message_id=None,
            created_at=now,
        ),
        Event(
            id=str(uuid4()),
            user_id=user_id,
            type="plan",
            title="计划学日语",
            content="今年想学完日语 N5。",
            occurred_at=now,
            source_message_id=None,
            created_at=now,
        ),
    ]
    episodic = [
        "我家有一只叫小白的猫",
        "周末喜欢去咖啡馆看书",
        "我的妻子叫李娜，做产品经理",
    ]
    relationships = [
        Relationship(
            id=str(uuid4()),
            user_id=user_id,
            name="李娜",
            role="妻子",
            attributes={"职业": "产品经理"},
            via=None,
            status="active",
            created_at=now,
        ),
    ]
    return PersonaSeed(
        profile=profile,
        events=events,
        episodic=episodic,
        relationships=relationships,
    )


async def seed_eval_persona(
    *,
    user_id: str,
    profile_repo: ProfileRepository,
    event_repo: EventRepository,
    episodic_repo: EpisodicRepository,
    relationship_repo: RelationshipRepository,
    seed: PersonaSeed | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> SeedSummary:
    """Wipe nothing; just upsert/insert the seed payload.

    Important: callers are expected to *first* purge the eval user's
    memory via the dev-only API endpoint before invoking this — the
    seeder itself doesn't delete (D6: services don't issue cross-user
    deletes; the wipe is handled at the API layer with auth gating).
    """
    plan = seed or default_seed(user_id)
    profile_set = False
    if plan.profile is not None:
        if plan.profile.user_id != user_id:
            plan = PersonaSeed(
                profile=plan.profile.model_copy(update={"user_id": user_id}),
                events=plan.events,
                episodic=plan.episodic,
                relationships=plan.relationships,
            )
        await profile_repo.upsert(plan.profile)
        profile_set = True

    for ev in plan.events:
        if ev.user_id != user_id:
            ev = ev.model_copy(update={"user_id": user_id})
        await event_repo.insert(ev)

    extra: dict[str, Any] = dict(metadata_extra or {})
    extra.setdefault("source", "eval_persona_seed")
    inserted_episodic = 0
    for fact in plan.episodic:
        if not fact.strip():
            continue
        await episodic_repo.add(fact, metadata={"id": str(uuid4()), **extra})
        inserted_episodic += 1

    for rel in plan.relationships:
        if rel.user_id != user_id:
            rel = rel.model_copy(update={"user_id": user_id})
        await relationship_repo.upsert(rel)

    return SeedSummary(
        user_id=user_id,
        profile_set=profile_set,
        events_inserted=len(plan.events),
        episodic_inserted=inserted_episodic,
        relationships_upserted=len(plan.relationships),
    )


__all__ = [
    "PersonaSeed",
    "SeedSummary",
    "default_seed",
    "seed_eval_persona",
]


# Re-exported for ``ServicesProtocol`` typed wiring.
def episodic_repo_supports_add(repo: EpisodicRepository) -> bool:
    return hasattr(repo, "add")


def event_repo_supports_insert(repo: EventRepository) -> bool:
    return hasattr(repo, "insert")


def profile_repo_supports_upsert(repo: ProfileRepository) -> bool:
    return hasattr(repo, "upsert")


def relationship_repo_supports_upsert(repo: RelationshipRepository) -> bool:
    return hasattr(repo, "upsert")


def support_check(repos: Sequence[Any]) -> list[str]:
    """Tiny diagnostic — used by the dev-only endpoint to surface
    which repo implementations are present (helpful in CI logs)."""
    out: list[str] = []
    for r in repos:
        out.append(type(r).__name__)
    return out
