"""Async runners executed by Celery tasks.

Splitting the async logic out of the Celery wrappers + the IO boundary
gives us:
- Direct unit tests against ``await runner(...)`` with fake repos.
- A single place per task to take/release ``AsyncSession`` and commit.

Each runner returns a small dict summarizing what happened — the Celery
task surfaces it back as the AsyncResult payload (handy in tests + logs).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.memory import Event, Relationship
from app.services.memory_extract.episodic_extractor import EpisodicExtractor
from app.services.memory_extract.event_extractor import EventExtractor
from app.services.memory_extract.profile_extractor import ProfileExtractor
from app.services.memory_extract.profile_merger import ProfileMerger
from app.services.memory_extract.relationship_extractor import RelationshipExtractor
from app.services.protocols import (
    BannedEntityRepository,
    EpisodicRepository,
    EventRepository,
    MessageRepository,
    ProfileRepository,
    RelationshipRepository,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Helpers shared by every runner
# ─────────────────────────────────────────────────────────────────


async def load_turn_context(
    *,
    message_repo: MessageRepository,
    conversation_id: str,
    message_id: str,
) -> tuple[Any, list[dict[str, str]]] | None:
    """Find the user message + a small window of priors.

    Returns ``(target_message, history_for_llm)`` or ``None`` when the
    target is missing / not authored by the user (worker should silently
    skip; the chat path may have been cancelled before persistence).
    """
    history = await message_repo.list_history(conversation_id, limit=12)
    target = next(
        (m for m in history if m.id == message_id and m.role == "user"),
        None,
    )
    if target is None:
        return None
    prior: list[dict[str, str]] = []
    for m in history:
        if m.id == message_id:
            break
        prior.append({"role": m.role, "content": m.content})
    return target, prior


def _hits_banned(text: str, banned: Sequence[str]) -> bool:
    if not banned:
        return False
    needle = text.lower()
    return any(b and b.lower() in needle for b in banned)


# ─────────────────────────────────────────────────────────────────
# Episodic
# ─────────────────────────────────────────────────────────────────


async def run_extract_episodic(
    *,
    extractor: EpisodicExtractor,
    message_repo: MessageRepository,
    episodic_repo: EpisodicRepository,
    banned_repo: BannedEntityRepository,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    facts = await extractor.extract(target.content, history=prior)
    if not facts:
        return {"status": "ok", "inserted": 0}

    banned = [b.entity for b in await banned_repo.list()]
    inserted: list[str] = []
    skipped_banned = 0
    for fact in facts:
        if _hits_banned(fact, banned):
            skipped_banned += 1
            continue
        mem_id = await episodic_repo.add(
            fact,
            metadata={
                "id": str(uuid.uuid4()),
                "source_message_id": message_id,
                "source": "extract_memory",
            },
        )
        if mem_id:
            inserted.append(mem_id)

    return {
        "status": "ok",
        "inserted": len(inserted),
        "skipped_banned": skipped_banned,
        "ids": inserted,
    }


# ─────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────


async def run_extract_profile(
    *,
    extractor: ProfileExtractor,
    message_repo: MessageRepository,
    profile_repo: ProfileRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    partial = await extractor.extract(target.content, history=prior)
    if not partial:
        return {"status": "ok", "fields": 0}

    merger = ProfileMerger()
    current = await profile_repo.get()
    seeded = dict(partial)
    if current is None:
        seeded.setdefault("user_id", user_id)
    merged = merger.merge(current=current, partial=seeded)
    await profile_repo.upsert(merged)

    return {"status": "ok", "fields": _count_partial_fields(partial)}


def _count_partial_fields(partial: dict[str, Any]) -> int:
    n = 0
    for value in partial.values():
        if isinstance(value, dict):
            n += sum(1 for v in value.values() if v not in (None, "", []))
        elif isinstance(value, list):
            n += len(value)
        elif value:
            n += 1
    return n


# ─────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────


async def run_extract_event(
    *,
    extractor: EventExtractor,
    message_repo: MessageRepository,
    event_repo: EventRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    candidates = await extractor.extract(target.content, history=prior)
    if not candidates:
        return {"status": "ok", "inserted": 0}

    inserted: list[str] = []
    now = datetime.now(UTC)
    for cand in candidates:
        event = Event(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=cand.type,
            title=cand.title[:200],
            content=cand.content,
            occurred_at=cand.occurred_at,
            source_message_id=message_id,
            created_at=now,
        )
        await event_repo.insert(event)
        inserted.append(event.id)

    return {"status": "ok", "inserted": len(inserted), "ids": inserted}


# ─────────────────────────────────────────────────────────────────
# Relationship
# ─────────────────────────────────────────────────────────────────


async def run_extract_relationship(
    *,
    extractor: RelationshipExtractor,
    message_repo: MessageRepository,
    relationship_repo: RelationshipRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    candidates = await extractor.extract(target.content, history=prior)
    if not candidates:
        return {"status": "ok", "inserted": 0}

    upserted: list[str] = []
    for cand in candidates:
        existing = await relationship_repo.find_by_name(cand.name)
        via_id: str | None = None
        if cand.via_name:
            via_rows = await relationship_repo.find_by_name(cand.via_name)
            via_id = via_rows[0].id if via_rows else None

        if existing:
            target_rel = existing[0]
            merged_attrs = dict(target_rel.attributes)
            merged_attrs.update(cand.attributes)
            rel = Relationship(
                schema_version=target_rel.schema_version,
                id=target_rel.id,
                user_id=user_id,
                name=target_rel.name,
                role=target_rel.role or cand.role,
                attributes=merged_attrs,
                via=target_rel.via or via_id,
                status="active",
                created_at=target_rel.created_at,
            )
        else:
            rel_id = str(uuid.uuid4())
            if via_id == rel_id:
                via_id = None
            rel = Relationship(
                id=rel_id,
                user_id=user_id,
                name=cand.name,
                role=cand.role,
                attributes=dict(cand.attributes),
                via=via_id,
            )
        await relationship_repo.upsert(rel)
        upserted.append(rel.id)

    return {"status": "ok", "upserted": len(upserted), "ids": upserted}
