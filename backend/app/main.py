"""FastAPI application factory + ASGI entry point.

Run locally:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

The lifespan builds:
- SQLAlchemy engine + sessionmaker (via factory)
- JwtCodec singleton
- LLMRouter with a Qwen client for the CHAT role
- A default in-memory IntentCache + ContractGuard + PromptComposer
- A ChatOrchestrator wiring all of the above
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from openai import AsyncOpenAI

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.eval import router as eval_router
from app.api.health import router as health_router
from app.api.memory import router as memory_router
from app.config import Settings, get_settings
from app.domain.llm import DEFAULT_MODELS, LLMRole
from app.infra.auth import JwtCodec
from app.infra.db.factory import make_engine, make_sessionmaker
from app.infra.llm import LLMRouter, MockLLMClient
from app.infra.llm.dashscope_embedder import DashScopeEmbedder
from app.infra.llm.mock_client import MockEmbeddingClient
from app.infra.llm.qwen_client import QwenClient
from app.infra.tasks import CeleryDispatcher, InMemoryDispatcher
from app.services.contract_guard import ContractGuard
from app.services.memory_router import (
    InMemoryIntentCache,
    LLMIntentClassifier,
    MemoryRouter,
)
from app.services.prompt_composer import PromptComposer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    engine = make_engine(settings.database_url, kind=settings.runtime_kind)
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.settings = settings

    # JWT codec
    app.state.jwt = JwtCodec(
        secret=settings.jwt_secret,
        algorithm=settings.jwt_alg,
        default_ttl=timedelta(minutes=settings.jwt_ttl_min),
    )

    # LLM clients
    llm_router = _build_llm_router(settings)
    app.state.llm_router = llm_router

    # Memory router (intent cache: in-memory by default; M3 swaps to Redis)
    intent_cache = InMemoryIntentCache()
    app.state.intent_cache = intent_cache
    classifier = LLMIntentClassifier(llm=llm_router.for_role(LLMRole.INTENT))
    memory_router = MemoryRouter(classifier=classifier, cache=intent_cache)
    app.state.memory_router = memory_router

    # Composer + guard + dispatcher + embedder (all stateless / process-wide)
    app.state.prompt_composer = PromptComposer(timezone=settings.prompt_timezone)
    app.state.contract_guard = ContractGuard()
    app.state.task_dispatcher = _build_dispatcher(settings)
    app.state.embedder = _build_embedder(settings)

    # NOTE: ChatOrchestrator is constructed PER REQUEST in api.chat because
    # its MemoryContextLoader depends on per-user, per-session repos.

    try:
        yield
    finally:
        await engine.dispose()


def _build_dispatcher(settings: Settings):
    """Pick CeleryDispatcher in prod / in_memory for dev & tests.

    The choice is driven by ``settings.task_dispatcher`` so unit tests
    and the in-memory smoke flow can opt out without dragging Celery
    bootstrapping into the API request path.
    """
    if settings.task_dispatcher == "celery":
        # Lazy import: keeps the celery_app construction off any code
        # path that doesn't actually dispatch (e.g. tests using
        # InMemoryDispatcher).
        from app.workers.celery_app import celery_app

        return CeleryDispatcher(celery_app=celery_app)
    return InMemoryDispatcher()


def _build_embedder(settings: Settings):
    """Pick a real DashScope embedder when an API key is configured.

    Otherwise fall back to ``MockEmbeddingClient`` so unit tests, health
    checks and chat flows that don't actually embed (CASUAL /
    KNOWLEDGE_TASK) keep working out of the box.
    """
    if settings.openai_api_key and settings.openai_api_key != "replace-me":
        client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
        return DashScopeEmbedder(
            client=client,
            model=DEFAULT_MODELS[LLMRole.EMBEDDING],
        )
    return MockEmbeddingClient()


def _build_llm_router(settings: Settings) -> LLMRouter:
    router = LLMRouter()
    if settings.openai_api_key and settings.openai_api_key != "replace-me":
        client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
        for role in (LLMRole.CHAT, LLMRole.INTENT, LLMRole.EXTRACT, LLMRole.CORRECTION, LLMRole.JUDGE):
            router.register(
                role,
                QwenClient(
                    role=role,
                    model=DEFAULT_MODELS[role],
                    client=client,
                    enable_thinking=settings.enable_thinking,
                ),
            )
    else:
        # Dev / unconfigured: register a mock that loudly fails on real use,
        # but lets the app boot for tests/healthchecks.
        for role in LLMRole:
            router.register(role, MockLLMClient(role=role, model=DEFAULT_MODELS[role]))
    return router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_title,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(chat_router)
    app.include_router(memory_router)
    app.include_router(eval_router)
    return app


app = create_app()
