"""FastAPI application factory + ASGI entry point.

Run locally:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.infra.db.factory import make_engine, make_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    engine = make_engine(settings.database_url, kind=settings.runtime_kind)
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.settings = settings
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_title,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    return app


app = create_app()
