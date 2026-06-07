"""In-memory fakes for service-layer tests.

These satisfy the Protocols in ``app.services.protocols`` without touching
SQLAlchemy or pgvector. They intentionally do less than the real repos —
just enough to drive M2 service tests deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.correction import (
    BannedEntity,
    CorrectionJudgement,
    CorrectionTarget,
    MemoryDeprecation,
)
from app.domain.memory import (
    EpisodicHit,
    EpisodicMemory,
    Event,
    Profile,
    Relationship,
)
from app.domain.prompt import PromptMeta
from app.domain.route import MemoryRoute

# ───────────────────────────────────────── memory repos ──


@dataclass
class FakeProfileRepo:
    user_id: str
    profile: Profile | None = None

    async def get(self) -> Profile | None:
        return self.profile

    async def upsert(self, profile: Profile) -> None:
        self.profile = profile

    async def merge(self, partial: dict[str, Any]) -> Profile:
        cur = self.profile or Profile(user_id=self.user_id)
        merged = cur.model_copy(update=partial)
        self.profile = merged
        return merged


@dataclass
class FakeEventRepo:
    user_id: str
    rows: list[Event] = field(default_factory=list)

    async def list_recent(self, limit: int = 10) -> list[Event]:
        return [e for e in self.rows if e.status == "active"][:limit]

    async def list_by_type(self, type_: str, limit: int = 10) -> list[Event]:
        return [e for e in self.rows if e.type == type_ and e.status == "active"][:limit]

    async def insert(self, event: Event) -> None:
        self.rows.append(event)

    async def deprecate(self, event_id: str, reason: str) -> None:
        for e in self.rows:
            if e.id == event_id:
                e.status = "deprecated"


@dataclass
class FakeEpisodicRepo:
    user_id: str
    rows: list[EpisodicMemory] = field(default_factory=list)
    search_results: list[EpisodicHit] = field(default_factory=list)

    async def add(self, text: str, metadata: dict[str, Any]) -> str:
        new_id = metadata.get("id", f"m_{len(self.rows)}")
        self.rows.append(
            EpisodicMemory(id=new_id, user_id=self.user_id, text=text, source=metadata.get("source"))
        )
        return new_id

    async def search(self, query: str, *, limit: int = 10) -> list[EpisodicHit]:
        _ = query
        return list(self.search_results[:limit])

    async def soft_delete(self, mem_id: str, reason: str) -> None:
        _ = reason
        for r in self.rows:
            if r.id == mem_id:
                r.status = "deprecated"

    async def list_recent(self, limit: int = 50) -> list[EpisodicMemory]:
        return [r for r in self.rows if r.status == "active"][:limit]


@dataclass
class FakeRelationshipRepo:
    user_id: str
    rows: list[Relationship] = field(default_factory=list)

    async def list_for_intent(self, route: MemoryRoute) -> list[Relationship]:
        # Mirror real repo: only surface for a specific subset of intents.
        from app.infra.repositories.relationship_repo import _RELATIONSHIP_FRIENDLY_INTENTS

        if route.intent not in _RELATIONSHIP_FRIENDLY_INTENTS:
            return []
        return [r for r in self.rows if r.status == "active"]

    async def find_by_name(self, name: str) -> list[Relationship]:
        return [r for r in self.rows if r.name == name and r.status == "active"]

    async def upsert(self, rel: Relationship) -> None:
        for i, r in enumerate(self.rows):
            if r.id == rel.id:
                self.rows[i] = rel
                return
        self.rows.append(rel)


@dataclass
class FakeBannedEntityRepo:
    user_id: str
    rows: list[BannedEntity] = field(default_factory=list)

    async def list(self) -> list[BannedEntity]:
        return list(self.rows)

    async def add_many(self, entities: Sequence[BannedEntity]) -> int:
        added = 0
        existing = {b.entity for b in self.rows}
        for e in entities:
            if e.entity not in existing:
                self.rows.append(e)
                existing.add(e.entity)
                added += 1
        return added


@dataclass
class FakeDeprecationRepo:
    user_id: str
    deprecated_episodic: set[str] = field(default_factory=set)
    rows: list[MemoryDeprecation] = field(default_factory=list)

    async def list_episodic_ids(self) -> set[str]:
        return set(self.deprecated_episodic)

    async def list_recent(self, limit: int = 50) -> list[MemoryDeprecation]:
        return list(self.rows[:limit])

    async def insert(self, dep: MemoryDeprecation) -> None:
        self.rows.append(dep)
        if dep.source == "episodic" and dep.action == "deprecate":
            self.deprecated_episodic.add(dep.ref_id)


# ───────────────────────────────────────── chat repos ──


@dataclass
class _ConvRow:
    id: str
    user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class FakeConversationRepo:
    user_id: str
    rows: dict[str, _ConvRow] = field(default_factory=dict)

    async def list_recent(self, limit: int = 20) -> list[_ConvRow]:
        return sorted(self.rows.values(), key=lambda r: r.updated_at, reverse=True)[:limit]

    async def get(self, conversation_id: str) -> _ConvRow | None:
        row = self.rows.get(conversation_id)
        if row is None or row.user_id != self.user_id:
            return None
        return row

    async def create(self, conversation_id: str, title: str | None = None) -> None:
        now = datetime.now(UTC)
        self.rows[conversation_id] = _ConvRow(
            id=conversation_id,
            user_id=self.user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )

    async def touch(self, conversation_id: str, when: datetime | None = None) -> None:
        row = self.rows.get(conversation_id)
        if row is None:
            return
        row.updated_at = when or datetime.now(UTC)


@dataclass
class FakeMessageRow:
    id: str
    conversation_id: str
    user_id: str
    role: str
    content: str
    meta_json: dict[str, Any] | None
    created_at: datetime


@dataclass
class FakeMessageRepo:
    user_id: str
    rows: list[FakeMessageRow] = field(default_factory=list)

    async def list_history(self, conversation_id: str, limit: int = 12) -> list[FakeMessageRow]:
        relevant = [r for r in self.rows if r.conversation_id == conversation_id and r.user_id == self.user_id]
        relevant.sort(key=lambda r: r.created_at)
        return relevant[-limit:]

    async def insert_user(
        self,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> None:
        self.rows.append(
            FakeMessageRow(
                id=message_id,
                conversation_id=conversation_id,
                user_id=self.user_id,
                role="user",
                content=content,
                meta_json=None,
                created_at=datetime.now(UTC),
            )
        )

    async def insert_assistant(
        self,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        meta: PromptMeta,
        partial: bool = False,
        error: str | None = None,
    ) -> None:
        meta_dump = meta.model_dump(mode="json")
        if partial:
            meta_dump["partial"] = True
        if error:
            meta_dump["_error"] = error
        self.rows.append(
            FakeMessageRow(
                id=message_id,
                conversation_id=conversation_id,
                user_id=self.user_id,
                role="assistant",
                content=content,
                meta_json=meta_dump,
                created_at=datetime.now(UTC),
            )
        )


# ───────────────────────────────────────── auth ──


@dataclass
class FakeUserStore:
    by_id: dict[str, Any] = field(default_factory=dict)
    by_email: dict[str, str] = field(default_factory=dict)

    async def get_by_email(self, email: str):
        uid = self.by_email.get(email)
        if uid is None:
            return None
        return self.by_id.get(uid)

    async def get_by_id(self, user_id: str):
        return self.by_id.get(user_id)

    async def create(self, record) -> None:
        self.by_id[record.id] = record
        self.by_email[record.email] = record.id

    async def update_password_hash(self, user_id: str, new_hash: str) -> None:
        rec = self.by_id[user_id]
        # UserRecord is frozen — replace.
        self.by_id[user_id] = type(rec)(
            id=rec.id,
            email=rec.email,
            password_hash=new_hash,
            display_name=rec.display_name,
            personality=rec.personality,
            is_active=rec.is_active,
            is_dev=rec.is_dev,
        )


# ───────────────────────────────────────── dispatcher ──


@dataclass
class FakeDispatcher:
    after_chat: list[dict[str, str]] = field(default_factory=list)
    correction: list[dict[str, Any]] = field(default_factory=list)

    async def dispatch_after_chat(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        intent: str,
    ) -> None:
        self.after_chat.append(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "intent": intent,
            }
        )

    async def dispatch_correction_cleanup(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        targets: Sequence[CorrectionTarget],
        judgements: Sequence[CorrectionJudgement] | None = None,
    ) -> None:
        self.correction.append(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "targets": [t.model_dump() for t in targets],
                "judgements": [j.model_dump() for j in (judgements or [])],
            }
        )
