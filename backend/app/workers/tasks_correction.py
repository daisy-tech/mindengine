"""Celery task wrapper for ``correction.cleanup``.

Per docs/rebuild/06-Subsystem-Correction.md §2 + §4.

The task is dispatched by the chat orchestrator whenever the routed
intent is ``correction``. It runs the full correction pipeline:

    extract_targets → search_candidates → judge each → extract_banned → apply

Repositories + extractors are constructed against a single AsyncSession
that we commit at the end of a successful run. Failures are retried on
transient LLM errors and otherwise swallowed (the chat path already
acknowledged the user; we don't want to surface a 500 here).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from celery.exceptions import Retry

from app.config import get_settings
from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMRateLimitError, LLMTimeoutError
from app.infra.repositories.banned_entity_repo import BannedEntityRepo
from app.infra.repositories.deprecation_repo import DeprecationRepo
from app.infra.repositories.episodic_repo import EpisodicRepo
from app.infra.repositories.event_repo import EventRepo
from app.infra.repositories.message_repo import MessageRepo
from app.infra.repositories.profile_repo import ProfileRepo
from app.infra.repositories.relationship_repo import RelationshipRepo
from app.services.correction import (
    BannedExtractor,
    CorrectionApplier,
    CorrectionCandidateSearcher,
    CorrectionJudge,
    CorrectionTargetExtractor,
)
from app.workers.celery_app import celery_app
from app.workers.runners import run_correction_cleanup
from app.workers.services import get_services

logger = logging.getLogger(__name__)


def _run_async(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


def _maybe_retry_on_transient(self_task, exc: Exception) -> None:
    if isinstance(exc, LLMRateLimitError | LLMTimeoutError):
        raise self_task.retry(exc=exc, countdown=10) from exc


async def _run_correction(
    user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    services = get_services()
    settings = get_settings()
    llm = services.llm_router.for_role(LLMRole.CORRECTION)
    extract_llm = services.llm_router.for_role(LLMRole.EXTRACT)

    target_extractor = CorrectionTargetExtractor(llm=llm)
    judge = CorrectionJudge(
        llm=llm,
        confidence_threshold=settings.correction_confidence_threshold,
    )
    banned_extractor = BannedExtractor(llm=extract_llm)

    async with services.sessionmaker() as session:
        message_repo = MessageRepo(session=session, user_id=user_id)
        searcher = CorrectionCandidateSearcher(
            profile_repo=ProfileRepo(session=session, user_id=user_id),
            event_repo=EventRepo(session=session, user_id=user_id),
            episodic_repo=EpisodicRepo(
                session=session, user_id=user_id, embedder=services.embedder
            ),
            relationship_repo=RelationshipRepo(session=session, user_id=user_id),
            candidate_limit=settings.correction_candidate_limit,
        )
        applier = CorrectionApplier(
            profile_repo=ProfileRepo(session=session, user_id=user_id),
            event_repo=EventRepo(session=session, user_id=user_id),
            episodic_repo=EpisodicRepo(
                session=session, user_id=user_id, embedder=services.embedder
            ),
            banned_repo=BannedEntityRepo(session=session, user_id=user_id),
            deprecation_repo=DeprecationRepo(session=session, user_id=user_id),
        )
        result = await run_correction_cleanup(
            target_extractor=target_extractor,
            searcher=searcher,
            judge=judge,
            banned_extractor=banned_extractor,
            applier=applier,
            message_repo=message_repo,
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        await session.commit()
        return result


@celery_app.task(
    name="correction.cleanup",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
)
def correction_cleanup(
    self, *, user_id: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    try:
        return _run_async(_run_correction(user_id, conversation_id, message_id))
    except Retry:
        raise
    except Exception as exc:
        _maybe_retry_on_transient(self, exc)
        logger.exception(
            "correction_cleanup failed user=%s msg=%s", user_id, message_id
        )
        return {"status": "error", "error": str(exc)}
