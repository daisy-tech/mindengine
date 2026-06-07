"""Message repository.

Per docs/rebuild/02-TDD.md §2.3 (D2): inserts must be safe to call from
a detached background task; we do not require a Request scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.prompt import PromptMeta
from app.infra.db.models import Message


@dataclass(frozen=True)
class MessageDTO:
    id: str
    conversation_id: str
    user_id: str
    role: str
    content: str
    meta_json: dict[str, Any] | None
    created_at: datetime


@dataclass
class MessageRepo:
    session: AsyncSession
    user_id: str

    async def list_history(self, conversation_id: str, limit: int = 12) -> list[MessageDTO]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.user_id == self.user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        # Reverse so callers get chronological order — they almost always need that.
        return [_to_dto(r) for r in reversed(rows)]

    async def insert_user(
        self,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> None:
        self.session.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                user_id=self.user_id,
                role="user",
                content=content,
            )
        )
        await self.session.flush()

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
        # Doc 02 §3.4 + lesson 3.4: persist a compact PromptMeta, NOT the full system prompt.
        meta_dump = meta.model_dump(mode="json", by_alias=False)
        if partial:
            meta_dump["partial"] = True
        self.session.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                user_id=self.user_id,
                role="assistant",
                content=content,
                meta_json=meta_dump,
                error=error,
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()


def _to_dto(row: Message) -> MessageDTO:
    return MessageDTO(
        id=row.id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        role=row.role,
        content=row.content,
        meta_json=row.meta_json,
        created_at=row.created_at,
    )
