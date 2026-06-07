"""Engine factory keyed by RUNTIME_KIND (web vs worker).

Per docs/rebuild/02-TDD.md §3.1 + 10-Lessons-Learned.md §1.2:
even with Postgres (no `database is locked`), separate engines per
process role are good practice for connection-pool and failure isolation.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

RuntimeKind = Literal["web", "worker"]


_POOL_PROFILES: dict[RuntimeKind, dict[str, int]] = {
    "web": {"pool_size": 10, "max_overflow": 5, "pool_recycle": 300},
    "worker": {"pool_size": 2, "max_overflow": 2, "pool_recycle": 60},
}


def make_engine(database_url: str, kind: RuntimeKind = "web") -> AsyncEngine:
    """Build an AsyncEngine sized for `kind`.

    The URL must be the asyncpg dialect (postgresql+asyncpg://...).
    """

    profile = _POOL_PROFILES[kind]
    return create_async_engine(
        database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        **profile,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
