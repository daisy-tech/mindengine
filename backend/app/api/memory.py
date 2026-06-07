"""Memory inspection API.

Per docs/rebuild/11-Roadmap.md M3 §4 + 02-TDD §2.5.

These endpoints are intentionally thin views over the four-layer repos
plus the audit tables (banned / deprecation). They exist so the front-end
profile page (and the eval harness) can render what the AI knows about
the user — the chat path itself never reads through this API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUserId, MemoryReposDep, SessionDep
from app.domain.correction import BannedEntity, MemoryDeprecation
from app.domain.memory import (
    EpisodicMemory,
    Event,
    Profile,
    Relationship,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


# ─────────────────────────────────────────────────────────────────
# DTOs
# ─────────────────────────────────────────────────────────────────


class BannedEntityDTO(BaseModel):
    entity: str
    reason: str = ""
    created_at: datetime


class AddBannedRequest(BaseModel):
    entities: list[str] = Field(min_length=1, max_length=50)
    reason: str = Field(default="", max_length=500)


class DeprecationDTO(BaseModel):
    id: int | None
    source: str
    ref_id: str
    reason: str
    action: str
    deprecated_at: datetime | None
    restored_at: datetime | None


class PatchProfileRequest(BaseModel):
    """Free-form partial — see ProfileMerger for which fields are
    accumulative vs override. Unknown keys are dropped silently."""

    basic: dict[str, Any] | None = None
    occupation: dict[str, Any] | None = None
    interests: list[str] | None = None
    family_structure: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────
# profile
# ─────────────────────────────────────────────────────────────────


@router.get("/profile", response_model=Profile | None)
async def get_profile(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
) -> Profile | None:
    _ = user_id  # repos already scoped
    return await repos.profile.get()


@router.patch("/profile", response_model=Profile)
async def patch_profile(
    body: PatchProfileRequest,
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    session: SessionDep,
) -> Profile:
    """Front-end profile editor target. Every change is treated as a
    *correction* (so it lands in user_corrections audit), then merged.
    """
    from app.services.memory_extract.profile_merger import ProfileMerger

    partial: dict[str, Any] = body.model_dump(exclude_none=True)
    if not partial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patch payload is empty",
        )
    partial["__source__"] = "correction"
    partial.setdefault("user_id", user_id)

    current = await repos.profile.get()
    merged = ProfileMerger().merge(current=current, partial=partial)
    await repos.profile.upsert(merged)
    await session.commit()
    return merged


# ─────────────────────────────────────────────────────────────────
# events
# ─────────────────────────────────────────────────────────────────


@router.get("/events", response_model=list[Event])
async def list_events(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    type: str | None = None,
    limit: int = 50,
) -> list[Event]:
    _ = user_id
    if limit <= 0 or limit > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="limit out of range"
        )
    if type:
        return await repos.event.list_by_type(type, limit=limit)
    return await repos.event.list_recent(limit=limit)


# ─────────────────────────────────────────────────────────────────
# episodic
# ─────────────────────────────────────────────────────────────────


@router.get("/episodic", response_model=list[EpisodicMemory])
async def list_episodic(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    limit: int = 50,
) -> list[EpisodicMemory]:
    _ = user_id
    if limit <= 0 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="limit out of range"
        )
    return await repos.episodic.list_recent(limit=limit)


@router.delete("/episodic/{mem_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episodic(
    mem_id: str,
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    session: SessionDep,
    reason: str = "user_deleted",
) -> None:
    await repos.episodic.soft_delete(mem_id, reason)
    await repos.deprecation.insert(
        MemoryDeprecation(
            user_id=user_id,
            source="episodic",
            ref_id=mem_id,
            reason=reason,
            action="deprecate",
        )
    )
    await session.commit()


# ─────────────────────────────────────────────────────────────────
# relationships
# ─────────────────────────────────────────────────────────────────


@router.get("/relationships", response_model=list[Relationship])
async def list_relationships(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
) -> list[Relationship]:
    _ = user_id
    from app.domain.route import (
        EventPolicy,
        Intent,
        IntentSource,
        MemoryDepth,
        MemoryRoute,
        Personality,
    )

    synthetic = MemoryRoute(
        intent=Intent.SELF_SUMMARY,
        intent_source=IntentSource.HARD_RULE,
        intent_confidence=1.0,
        personality=Personality.BALANCED,
        memory_depth=MemoryDepth.WIDE,
        load_layers=["relationships"],
        max_explicit_memories=5,
        event_policy=EventPolicy.NONE,
    )
    return await repos.relationship.list_for_intent(synthetic)


# ─────────────────────────────────────────────────────────────────
# banned entities
# ─────────────────────────────────────────────────────────────────


@router.get("/banned-entities", response_model=list[BannedEntityDTO])
async def list_banned(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
) -> list[BannedEntityDTO]:
    _ = user_id
    from datetime import UTC

    rows = await repos.banned.list()
    return [
        BannedEntityDTO(
            entity=b.entity,
            reason=b.reason,
            created_at=b.created_at or datetime.now(UTC),
        )
        for b in rows
    ]


@router.post(
    "/banned-entities",
    status_code=status.HTTP_201_CREATED,
    response_model=dict[str, int],
)
async def add_banned(
    body: AddBannedRequest,
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    session: SessionDep,
) -> dict[str, int]:
    cleaned = {e.strip() for e in body.entities if e and e.strip()}
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entities must contain at least one non-empty string",
        )
    inserted = await repos.banned.add_many(
        [
            BannedEntity(user_id=user_id, entity=e, reason=body.reason)
            for e in cleaned
        ]
    )
    await session.commit()
    return {"inserted": inserted}


# ─────────────────────────────────────────────────────────────────
# deprecations
# ─────────────────────────────────────────────────────────────────


@router.get("/deprecations", response_model=list[DeprecationDTO])
async def list_deprecations(
    user_id: CurrentUserId,
    repos: MemoryReposDep,
    limit: int = 50,
) -> list[DeprecationDTO]:
    _ = user_id
    if limit <= 0 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="limit out of range"
        )
    rows = await repos.deprecation.list_recent(limit=limit)
    return [
        DeprecationDTO(
            id=r.id,
            source=r.source,
            ref_id=r.ref_id,
            reason=r.reason,
            action=r.action,
            deprecated_at=r.deprecated_at,
            restored_at=r.restored_at,
        )
        for r in rows
    ]
