"""User store backing AuthService.

Outside the per-user repo pattern because the auth flow needs to look
users up by email *before* a user_id is known.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.route import Personality
from app.infra.db.models import User
from app.services.auth.service import UserRecord


@dataclass
class UserRepo:
    session: AsyncSession

    async def get_by_email(self, email: str) -> UserRecord | None:
        stmt = select(User).where(User.email == email)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row else None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        row = await self.session.get(User, user_id)
        return _to_record(row) if row else None

    async def create(self, record: UserRecord) -> None:
        self.session.add(
            User(
                id=record.id,
                email=record.email,
                password_hash=record.password_hash,
                display_name=record.display_name,
                personality=record.personality.value,
                is_active=record.is_active,
                is_dev=record.is_dev,
            )
        )
        await self.session.flush()

    async def update_password_hash(self, user_id: str, new_hash: str) -> None:
        user = await self.session.get(User, user_id)
        if user is None:
            raise LookupError(f"User {user_id!r} not found")
        user.password_hash = new_hash
        await self.session.flush()


def _to_record(row: User) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        display_name=row.display_name,
        personality=Personality(row.personality),
        is_active=row.is_active,
        is_dev=row.is_dev,
    )
