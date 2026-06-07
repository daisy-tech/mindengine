"""Celery tasks: ``after_chat`` fan-out + 4 extractor tasks.

Per docs/rebuild/02-TDD.md §2.2 + 03 §6:
- ``after_chat`` is the single entrypoint the chat orchestrator dispatches
  to. It looks at the intent and decides which extractor tasks to enqueue.
- Each ``extract_*`` task wraps the matching async runner.
- Tasks are idempotent at the row level (UUID primary keys); retries
  rerun the LLM but rarely produce duplicates worth caring about.

The Celery decorators are thin: every task opens a single AsyncSession,
constructs the relevant repos + extractor, calls into ``runners`` (pure
business logic), and commits on success. Business logic itself is in
``app.workers.runners``, which is unit-tested without Celery.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from celery.exceptions import Retry

from app.domain.llm import LLMRole
from app.domain.route import Intent
from app.infra.llm.exceptions import LLMRateLimitError, LLMTimeoutError
from app.infra.repositories.banned_entity_repo import BannedEntityRepo
from app.infra.repositories.episodic_repo import EpisodicRepo
from app.infra.repositories.event_repo import EventRepo
from app.infra.repositories.message_repo import MessageRepo
from app.infra.repositories.profile_repo import ProfileRepo
from app.infra.repositories.relationship_repo import RelationshipRepo
from app.services.memory_extract.dispatch_targets import targets_for
from app.services.memory_extract.episodic_extractor import EpisodicExtractor
from app.services.memory_extract.event_extractor import EventExtractor
from app.services.memory_extract.profile_extractor import ProfileExtractor
from app.services.memory_extract.relationship_extractor import RelationshipExtractor
from app.workers.celery_app import celery_app
from app.workers.runners import (
    run_extract_episodic,
    run_extract_event,
    run_extract_profile,
    run_extract_relationship,
)
from app.workers.services import get_services

logger = logging.getLogger(__name__)


def _run_async(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


def _maybe_retry_on_transient(self_task, exc: Exception) -> None:
    if isinstance(exc, LLMRateLimitError | LLMTimeoutError):
        raise self_task.retry(exc=exc, countdown=10) from exc


# ─────────────────────────────────────────────────────────────────
# after_chat — fan-out
# ─────────────────────────────────────────────────────────────────


@celery_app.task(name="memory.after_chat", bind=True, max_retries=0)
def after_chat(
    self,
    *,
    user_id: str,
    conversation_id: str,
    message_id: str,
    intent: str,
) -> dict[str, Any]:
    try:
        intent_enum = Intent(intent)
    except ValueError:
        logger.warning("after_chat: unknown intent %r → CASUAL", intent)
        intent_enum = Intent.CASUAL

    layers = targets_for(intent_enum)
    dispatched: list[str] = []
    payload = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }

    if "episodic" in layers:
        extract_episodic.apply_async(kwargs=payload)
        dispatched.append("episodic")
    if "profile" in layers:
        extract_profile.apply_async(kwargs=payload)
        dispatched.append("profile")
    if "event" in layers:
        extract_event.apply_async(kwargs=payload)
        dispatched.append("event")
    if "relationship" in layers:
        extract_relationship.apply_async(kwargs=payload)
        dispatched.append("relationship")

    return {"intent": intent_enum.value, "dispatched": dispatched}


# ─────────────────────────────────────────────────────────────────
# Extractor tasks (one per layer)
# ─────────────────────────────────────────────────────────────────


async def _run_episodic(user_id: str, conversation_id: str, message_id: str):
    services = get_services()
    extractor = EpisodicExtractor(llm=services.llm_router.for_role(LLMRole.EXTRACT))
    async with services.sessionmaker() as session:
        result = await run_extract_episodic(
            extractor=extractor,
            message_repo=MessageRepo(session=session, user_id=user_id),
            episodic_repo=EpisodicRepo(
                session=session, user_id=user_id, embedder=services.embedder
            ),
            banned_repo=BannedEntityRepo(session=session, user_id=user_id),
            conversation_id=conversation_id,
            message_id=message_id,
        )
        await session.commit()
        return result


async def _run_profile(user_id: str, conversation_id: str, message_id: str):
    services = get_services()
    extractor = ProfileExtractor(llm=services.llm_router.for_role(LLMRole.EXTRACT))
    async with services.sessionmaker() as session:
        result = await run_extract_profile(
            extractor=extractor,
            message_repo=MessageRepo(session=session, user_id=user_id),
            profile_repo=ProfileRepo(session=session, user_id=user_id),
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        await session.commit()
        return result


async def _run_event(user_id: str, conversation_id: str, message_id: str):
    services = get_services()
    extractor = EventExtractor(llm=services.llm_router.for_role(LLMRole.EXTRACT))
    async with services.sessionmaker() as session:
        result = await run_extract_event(
            extractor=extractor,
            message_repo=MessageRepo(session=session, user_id=user_id),
            event_repo=EventRepo(session=session, user_id=user_id),
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        await session.commit()
        return result


async def _run_relationship(user_id: str, conversation_id: str, message_id: str):
    services = get_services()
    extractor = RelationshipExtractor(
        llm=services.llm_router.for_role(LLMRole.EXTRACT)
    )
    async with services.sessionmaker() as session:
        result = await run_extract_relationship(
            extractor=extractor,
            message_repo=MessageRepo(session=session, user_id=user_id),
            relationship_repo=RelationshipRepo(session=session, user_id=user_id),
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        await session.commit()
        return result


@celery_app.task(name="memory.extract_episodic", bind=True, max_retries=3, default_retry_delay=15)
def extract_episodic(
    self, *, user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    try:
        return _run_async(_run_episodic(user_id, conversation_id, message_id))
    except Retry:
        raise
    except Exception as exc:
        _maybe_retry_on_transient(self, exc)
        logger.exception("extract_episodic failed user=%s msg=%s", user_id, message_id)
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="memory.extract_profile", bind=True, max_retries=3, default_retry_delay=15)
def extract_profile(
    self, *, user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    try:
        return _run_async(_run_profile(user_id, conversation_id, message_id))
    except Retry:
        raise
    except Exception as exc:
        _maybe_retry_on_transient(self, exc)
        logger.exception("extract_profile failed user=%s msg=%s", user_id, message_id)
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="memory.extract_event", bind=True, max_retries=3, default_retry_delay=15)
def extract_event(
    self, *, user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    try:
        return _run_async(_run_event(user_id, conversation_id, message_id))
    except Retry:
        raise
    except Exception as exc:
        _maybe_retry_on_transient(self, exc)
        logger.exception("extract_event failed user=%s msg=%s", user_id, message_id)
        return {"status": "error", "error": str(exc)}


@celery_app.task(
    name="memory.extract_relationship", bind=True, max_retries=3, default_retry_delay=15
)
def extract_relationship(
    self, *, user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    try:
        return _run_async(_run_relationship(user_id, conversation_id, message_id))
    except Retry:
        raise
    except Exception as exc:
        _maybe_retry_on_transient(self, exc)
        logger.exception(
            "extract_relationship failed user=%s msg=%s", user_id, message_id
        )
        return {"status": "error", "error": str(exc)}
