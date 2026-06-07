"""FastAPI dependency providers.

Wire singletons (engine/sessionmaker/router/composer/guard/llm) constructed
during the app lifespan into per-request injectable callables.

Per docs/rebuild/02-TDD.md §6: ``current_user`` enforces authentication
and falls back to a dev-mode header when ``DEV_MODE=true`` (D6). All
production endpoints MUST depend on ``current_user``, never directly on
``X-Dev-User-Id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.infra.auth import JwtCodec, JwtError
from app.infra.db.session import session_scope

# ─────────────────────────────────────────────── settings / session ──


def get_settings_dep(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise RuntimeError("App.state.settings is unset; check lifespan setup")
    return settings


def _get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sm = getattr(request.app.state, "sessionmaker", None)
    if sm is None:
        raise RuntimeError("App.state.sessionmaker is unset; check lifespan setup")
    return sm


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sm = _get_sessionmaker(request)
    async with session_scope(sm) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


# ─────────────────────────────────────────────── auth ──


_bearer_scheme = HTTPBearer(auto_error=False)


def _get_jwt(request: Request) -> JwtCodec:
    codec = getattr(request.app.state, "jwt", None)
    if codec is None:
        raise RuntimeError("App.state.jwt is unset; check lifespan setup")
    return codec


async def current_user(
    request: Request,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
    x_dev_user_id: Annotated[str | None, Header(alias="X-Dev-User-Id")] = None,
) -> str:
    """Returns the authenticated user_id, or raises 401.

    Order of checks:
      1. Dev override (only when settings.dev_mode=True): accept the
         X-Dev-User-Id header verbatim. Logged with a warning.
      2. Bearer JWT: decode via app.state.jwt and return the `sub` claim.
    """
    if settings.dev_mode and x_dev_user_id:
        return x_dev_user_id.strip()

    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return _get_jwt(request).decode(creds.credentials)
    except JwtError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


CurrentUserId = Annotated[str, Depends(current_user)]


# ─────────────────────────────────────────────── singletons ──


def get_intent_cache(request: Request):
    cache = getattr(request.app.state, "intent_cache", None)
    if cache is None:
        raise RuntimeError("App.state.intent_cache is unset; check lifespan setup")
    return cache


def build_chat_orchestrator_factory(request: Request):
    """Returns a callable that builds a per-request ChatOrchestrator.

    Importable factory keeps api.chat free of construction noise.
    """
    from app.domain.llm import LLMRole
    from app.services.chat import ChatOrchestrator
    from app.services.memory_context import MemoryContextLoader

    state = request.app.state

    def make(
        *,
        session,
        user_id: str,
        embedder,
    ):
        from app.infra.repositories import (
            BannedEntityRepo,
            DeprecationRepo,
            EpisodicRepo,
            EventRepo,
            ProfileRepo,
            RelationshipRepo,
        )

        loader = MemoryContextLoader(
            profile_repo=ProfileRepo(session=session, user_id=user_id),
            event_repo=EventRepo(session=session, user_id=user_id),
            episodic_repo=EpisodicRepo(session=session, user_id=user_id, embedder=embedder),
            relationship_repo=RelationshipRepo(session=session, user_id=user_id),
            banned_repo=BannedEntityRepo(session=session, user_id=user_id),
            deprecation_repo=DeprecationRepo(session=session, user_id=user_id),
        )
        return ChatOrchestrator(
            router=state.memory_router,
            loader=loader,
            composer=state.prompt_composer,
            guard=state.contract_guard,
            chat_llm=state.llm_router.for_role(LLMRole.CHAT),
            dispatcher=state.task_dispatcher,
        )

    return make
