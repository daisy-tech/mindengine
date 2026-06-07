"""Memory-deprecation repository (correction audit log)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.correction import MemoryDeprecation
from app.infra.db.models import MemoryDeprecationRow


@dataclass
class DeprecationRepo:
    session: AsyncSession
    user_id: str

    async def list_episodic_ids(self) -> set[str]:
        """Active deprecations where source='episodic' — these refs must be
        excluded from any episodic search result the loader hands to the
        prompt composer (lesson 5.4 / doc 06 §5).
        """
        stmt = select(MemoryDeprecationRow.ref_id).where(
            MemoryDeprecationRow.user_id == self.user_id,
            MemoryDeprecationRow.source == "episodic",
            MemoryDeprecationRow.action == "deprecate",
            MemoryDeprecationRow.restored_at.is_(None),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return set(rows)

    async def list_recent(self, limit: int = 50) -> list[MemoryDeprecation]:
        stmt = (
            select(MemoryDeprecationRow)
            .where(MemoryDeprecationRow.user_id == self.user_id)
            .order_by(MemoryDeprecationRow.deprecated_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            MemoryDeprecation(
                schema_version=1,
                id=r.id,
                user_id=r.user_id,
                source=r.source,  # type: ignore[arg-type]
                ref_id=r.ref_id,
                original_text=r.original_text,
                new_text=r.new_text,
                reason=r.reason or "",
                correction_conversation_id=r.correction_conversation_id,
                correction_turn_id=r.correction_turn_id,
                llm_confidence=r.llm_confidence,
                action=r.action,  # type: ignore[arg-type]
                deprecated_at=r.deprecated_at,
                restored_at=r.restored_at,
            )
            for r in rows
        ]

    async def insert(self, dep: MemoryDeprecation) -> None:
        if dep.user_id != self.user_id:
            raise ValueError("DeprecationRepo refuses cross-user insert (D6)")
        self.session.add(
            MemoryDeprecationRow(
                user_id=dep.user_id,
                source=dep.source,
                ref_id=dep.ref_id,
                original_text=dep.original_text,
                new_text=dep.new_text,
                reason=dep.reason,
                correction_conversation_id=dep.correction_conversation_id,
                correction_turn_id=dep.correction_turn_id,
                llm_confidence=dep.llm_confidence,
                action=dep.action,
            )
        )
        await self.session.flush()
