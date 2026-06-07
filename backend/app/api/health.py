"""Health-check endpoints.

`/healthz` is a liveness probe. `/readyz` checks Postgres + pgvector
connectivity (per M1 acceptance: a `SELECT '[1,2,3]'::vector` round-trip
proves pgvector is loaded).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session

router = APIRouter(tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe (Postgres + pgvector)")
async def readyz(session: SessionDep) -> JSONResponse:
    checks: dict[str, Any] = {}
    try:
        # Postgres connectivity
        await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"

        # pgvector extension loaded — cast a literal to vector type
        await session.execute(text("SELECT '[1,2,3]'::vector"))
        checks["pgvector"] = "ok"

        return JSONResponse({"status": "ok", "checks": checks})
    except Exception as e:  # noqa: BLE001
        checks["error"] = repr(e)
        return JSONResponse(
            {"status": "fail", "checks": checks},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
