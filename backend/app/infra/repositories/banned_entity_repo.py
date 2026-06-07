"""Banned-entity repository (Postgres)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.correction import BannedEntity
from app.infra.db.models import BannedEntityRow


@dataclass
class BannedEntityRepo:
    session: AsyncSession
    user_id: str

    async def list(self) -> list[BannedEntity]:
        stmt = select(BannedEntityRow).where(BannedEntityRow.user_id == self.user_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            BannedEntity(
                user_id=r.user_id,
                entity=r.entity,
                reason=r.reason or "",
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def add_many(self, entities: Sequence[BannedEntity]) -> int:
        """Idempotent insert via ON CONFLICT DO NOTHING. Returns rows added."""
        if not entities:
            return 0
        for ent in entities:
            if ent.user_id != self.user_id:
                raise ValueError("BannedEntityRepo refuses cross-user write (D6)")
        rows = [
            {
                "user_id": e.user_id,
                "entity": e.entity,
                "reason": e.reason or None,
            }
            for e in entities
        ]
        stmt = pg_insert(BannedEntityRow).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[BannedEntityRow.user_id, BannedEntityRow.entity]
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0
