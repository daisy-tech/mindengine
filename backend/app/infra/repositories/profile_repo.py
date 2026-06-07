"""Profile repository (Postgres JSONB blob)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import (
    BasicInfo,
    FamilyHint,
    Occupation,
    Profile,
    UserCorrection,
)
from app.infra.db.models import ProfileRow


@dataclass
class ProfileRepo:
    session: AsyncSession
    user_id: str

    async def get(self) -> Profile | None:
        row = await self.session.get(ProfileRow, self.user_id)
        if row is None:
            return None
        return _from_json(row.data_json | {"user_id": self.user_id, "updated_at": row.updated_at})

    async def upsert(self, profile: Profile) -> None:
        if profile.user_id != self.user_id:
            raise ValueError("ProfileRepo refuses cross-user upsert (D6)")
        row = await self.session.get(ProfileRow, self.user_id)
        data = profile.model_dump(mode="json")
        # We never store user_id inside data_json — it's already the PK.
        data.pop("user_id", None)
        if row is None:
            self.session.add(
                ProfileRow(
                    user_id=self.user_id,
                    data_json=data,
                    schema_version=profile.schema_version,
                )
            )
        else:
            row.data_json = data
            row.schema_version = profile.schema_version
        await self.session.flush()

    async def merge(self, partial: dict[str, Any]) -> Profile:
        current = await self.get() or Profile(user_id=self.user_id)
        merged = current.model_copy(update=partial)
        merged.updated_at = datetime.now(UTC)
        await self.upsert(merged)
        return merged


def _from_json(data: dict[str, Any]) -> Profile:
    # Be permissive: the on-disk JSON may be from an older schema_version.
    return Profile(
        schema_version=data.get("schema_version", 1),
        user_id=data["user_id"],
        basic=BasicInfo(**(data.get("basic") or {})),
        interests=list(data.get("interests") or []),
        occupation=Occupation(**(data.get("occupation") or {})),
        family_structure=FamilyHint(**(data.get("family_structure") or {})),
        extra=dict(data.get("extra") or {}),
        user_corrections=[UserCorrection(**uc) for uc in (data.get("user_corrections") or [])],
        updated_at=data.get("updated_at"),
    )
