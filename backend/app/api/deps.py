"""FastAPI dependencies.

Wires the singletons (engine + sessionmaker) constructed at app startup
into per-request injectable callables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.db.session import session_scope


def _get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sm = getattr(request.app.state, "sessionmaker", None)
    if sm is None:
        raise RuntimeError(
            "App.state.sessionmaker is not set; check app.main lifespan setup."
        )
    return sm


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sm = _get_sessionmaker(request)
    async with session_scope(sm) as session:
        yield session
