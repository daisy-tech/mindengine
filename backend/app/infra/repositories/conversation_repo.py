"""Conversation repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import Conversation


@dataclass(frozen=True)
class ConversationDTO:
    id: str
    user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class ConversationRepo:
    session: AsyncSession
    user_id: str

    async def list_recent(self, limit: int = 20) -> list[ConversationDTO]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == self.user_id, Conversation.archived.is_(False))
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_dto(r) for r in rows]

    async def get(self, conversation_id: str) -> ConversationDTO | None:
        row = await self.session.get(Conversation, conversation_id)
        if row is None or row.user_id != self.user_id:
            # Return None rather than raising — avoids leaking existence
            # of a conversation that belongs to a different user (D6).
            return None
        return _to_dto(row)

    async def create(self, conversation_id: str, title: str | None = None) -> None:
        now = datetime.now(UTC)
        self.session.add(
            Conversation(
                id=conversation_id,
                user_id=self.user_id,
                title=title,
                created_at=now,
                updated_at=now,
            )
        )
        await self.session.flush()

    async def touch(self, conversation_id: str, when: datetime | None = None) -> None:
        row = await self.session.get(Conversation, conversation_id)
        if row is None or row.user_id != self.user_id:
            return
        row.updated_at = when or datetime.now(UTC)


def _to_dto(row: Conversation) -> ConversationDTO:
    return ConversationDTO(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
