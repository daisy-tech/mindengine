"""Event repository (`events` table)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import Event
from app.infra.db.models import EventRow


@dataclass
class EventRepo:
    session: AsyncSession
    user_id: str

    async def list_recent(self, limit: int = 10) -> list[Event]:
        stmt = (
            select(EventRow)
            .where(EventRow.user_id == self.user_id, EventRow.status == "active")
            .order_by(EventRow.occurred_at.desc().nullslast(), EventRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_event(r) for r in rows]

    async def list_by_type(self, type_: str, limit: int = 10) -> list[Event]:
        stmt = (
            select(EventRow)
            .where(
                EventRow.user_id == self.user_id,
                EventRow.type == type_,
                EventRow.status == "active",
            )
            .order_by(EventRow.occurred_at.desc().nullslast(), EventRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_event(r) for r in rows]

    async def insert(self, event: Event) -> None:
        if event.user_id != self.user_id:
            raise ValueError("EventRepo refuses cross-user insert (D6)")
        self.session.add(
            EventRow(
                id=event.id,
                user_id=event.user_id,
                type=event.type,
                title=event.title,
                content=event.content,
                occurred_at=event.occurred_at,
                status=event.status,
                source_message_id=event.source_message_id,
                schema_version=event.schema_version,
                created_at=event.created_at or datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def deprecate(self, event_id: str, reason: str) -> None:
        # NOTE: ``reason`` is logged in memory_deprecations; here we only flip status.
        _ = reason  # surfaced via deprecations table — see DeprecationRepo.insert
        stmt = (
            update(EventRow)
            .where(EventRow.user_id == self.user_id, EventRow.id == event_id)
            .values(status="deprecated")
        )
        await self.session.execute(stmt)


def _to_event(row: EventRow) -> Event:
    return Event(
        schema_version=row.schema_version,
        id=row.id,
        user_id=row.user_id,
        type=row.type,  # type: ignore[arg-type]
        title=row.title,
        content=row.content,
        occurred_at=row.occurred_at,
        status=row.status,  # type: ignore[arg-type]
        source_message_id=row.source_message_id,
        created_at=row.created_at,
    )
