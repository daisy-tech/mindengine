"""Engine factory keyed by RUNTIME_KIND (web vs worker).

Per docs/rebuild/02-TDD.md §3.1 + 10-Lessons-Learned.md §1.2:
even with Postgres (no `database is locked`), separate engines per
process role are good practice for connection-pool and failure isolation.

Worker note (asyncpg + Celery prefork):
    Celery tasks here run via ``asyncio.run(coro)``, which creates a
    fresh event loop for every task. asyncpg connections are bound to
    the loop that created them, so pooling them across tasks raises
    ``RuntimeError: got Future attached to a different loop`` on the
    first task that re-checks-out a stale connection. The standard
    fix is ``NullPool`` — open + close a real connection per task.
    The web engine still pools normally because uvicorn runs a single
    long-lived loop per process.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

RuntimeKind = Literal["web", "worker"]


def _engine_kwargs(kind: RuntimeKind) -> dict[str, Any]:
    if kind == "worker":
        # NullPool is incompatible with pool_size / max_overflow / pool_recycle.
        return {"poolclass": NullPool}
    return {
        "pool_size": 10,
        "max_overflow": 5,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }


def make_engine(database_url: str, kind: RuntimeKind = "web") -> AsyncEngine:
    """Build an AsyncEngine sized for `kind`.

    The URL must be the asyncpg dialect (postgresql+asyncpg://...).
    """

    return create_async_engine(
        database_url,
        echo=False,
        future=True,
        **_engine_kwargs(kind),
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
