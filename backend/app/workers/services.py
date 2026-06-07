"""Worker-process service singletons.

Celery worker concurrency uses prefork (one OS process per slot). We
boot DB engine + LLM clients per-process (lazily, on first task) and
keep them alive for the worker's lifetime. Each task opens its own
``AsyncSession`` and never shares it across tasks (lesson 1.2).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.domain.llm import DEFAULT_MODELS, LLMRole
from app.infra.db.factory import make_engine, make_sessionmaker
from app.infra.llm import LLMRouter, MockLLMClient
from app.infra.llm.dashscope_embedder import DashScopeEmbedder
from app.infra.llm.mock_client import MockEmbeddingClient
from app.infra.llm.qwen_client import QwenClient
from app.services.protocols import EmbeddingClient


@dataclass
class WorkerServices:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    llm_router: LLMRouter
    embedder: EmbeddingClient


_services: WorkerServices | None = None
_services_lock = threading.Lock()


def get_services() -> WorkerServices:
    """Return process-singleton services; build on first call."""
    global _services
    if _services is not None:
        return _services
    with _services_lock:
        if _services is not None:
            return _services
        _services = _build()
        return _services


def reset_services_for_tests() -> None:
    """Drop the cached singleton; used by unit tests that inject fakes."""
    global _services
    with _services_lock:
        _services = None


def set_services_for_tests(services: WorkerServices) -> None:
    """Inject pre-built services (unit-test helper). Not for prod."""
    global _services
    with _services_lock:
        _services = services


def _build() -> WorkerServices:
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="worker")
    sm = make_sessionmaker(engine)
    return WorkerServices(
        settings=settings,
        engine=engine,
        sessionmaker=sm,
        llm_router=_build_router(settings),
        embedder=_build_embedder(settings),
    )


def _build_router(settings: Settings) -> LLMRouter:
    router = LLMRouter()
    if settings.openai_api_key and settings.openai_api_key != "replace-me":
        client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
        for role in (
            LLMRole.CHAT,
            LLMRole.INTENT,
            LLMRole.EXTRACT,
            LLMRole.CORRECTION,
            LLMRole.JUDGE,
        ):
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
        for role in LLMRole:
            router.register(role, MockLLMClient(role=role, model=DEFAULT_MODELS[role]))
    return router


def _build_embedder(settings: Settings) -> EmbeddingClient:
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
