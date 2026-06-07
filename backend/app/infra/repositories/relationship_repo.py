"""Relationship repository."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import Relationship
from app.domain.route import Intent, MemoryRoute
from app.infra.db.models import RelationshipRow

# Intents where it's safe to surface relationships (doc 03 §6 policy).
_RELATIONSHIP_FRIENDLY_INTENTS = frozenset(
    {Intent.RELATIONSHIP_TOPIC, Intent.SELF_SUMMARY, Intent.EMOTIONAL_SUPPORT}
)


@dataclass
class RelationshipRepo:
    session: AsyncSession
    user_id: str

    async def list_for_intent(self, route: MemoryRoute) -> list[Relationship]:
        if route.intent not in _RELATIONSHIP_FRIENDLY_INTENTS:
            return []
        stmt = (
            select(RelationshipRow)
            .where(
                RelationshipRow.user_id == self.user_id,
                RelationshipRow.status == "active",
            )
            .order_by(RelationshipRow.created_at.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_rel(r) for r in rows]

    async def find_by_name(self, name: str) -> list[Relationship]:
        stmt = (
            select(RelationshipRow)
            .where(
                RelationshipRow.user_id == self.user_id,
                RelationshipRow.name == name,
                RelationshipRow.status == "active",
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_rel(r) for r in rows]

    async def upsert(self, rel: Relationship) -> None:
        if rel.user_id != self.user_id:
            raise ValueError("RelationshipRepo refuses cross-user upsert (D6)")
        stmt = pg_insert(RelationshipRow).values(
            id=rel.id,
            user_id=rel.user_id,
            name=rel.name,
            role=rel.role,
            attributes_json=rel.attributes,
            via=rel.via,
            status=rel.status,
            schema_version=rel.schema_version,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RelationshipRow.id],
            set_={
                "name": rel.name,
                "role": rel.role,
                "attributes_json": rel.attributes,
                "via": rel.via,
                "status": rel.status,
                "schema_version": rel.schema_version,
            },
        )
        await self.session.execute(stmt)


def _to_rel(row: RelationshipRow) -> Relationship:
    return Relationship(
        schema_version=row.schema_version,
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        role=row.role,
        attributes=dict(row.attributes_json or {}),
        via=row.via,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
    )
