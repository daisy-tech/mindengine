"""Celery-backed TaskDispatcher.

Translates the async ``TaskDispatcher`` protocol into ``apply_async``
calls on the Celery app. Sending is non-blocking (``apply_async`` only
publishes to the broker), but Celery's API is synchronous, so we run it
inside ``asyncio.to_thread`` to avoid blocking the SSE event loop on
broker network IO.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.correction import CorrectionJudgement, CorrectionTarget

logger = logging.getLogger(__name__)


@dataclass
class CeleryDispatcher:
    """Implements ``services.protocols.TaskDispatcher`` against Celery."""

    celery_app: Any  # type: celery.Celery — kept loose to avoid hard dep at import

    async def dispatch_after_chat(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        intent: str,
    ) -> None:
        await self._send(
            "memory.after_chat",
            kwargs={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "intent": intent,
            },
        )

    async def dispatch_correction_cleanup(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        targets: Sequence[CorrectionTarget] | None = None,
        judgements: Sequence[CorrectionJudgement] | None = None,
    ) -> None:
        """Publish ``correction.cleanup`` for one user-correction message.

        Per docs/rebuild/06-Subsystem-Correction.md §2: the worker
        re-derives the correction targets from the persisted message,
        so we don't need to ship them through the broker. The
        ``targets`` / ``judgements`` parameters are kept on the
        protocol for forward-compat (debug / replay flows).
        """

        _ = targets  # accepted but not forwarded (worker reads from DB)
        _ = judgements
        await self._send(
            "correction.cleanup",
            kwargs={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        )

    async def _send(self, name: str, *, kwargs: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(
                self.celery_app.send_task, name, kwargs=kwargs
            )
        except Exception as exc:  # pragma: no cover — broker outages
            # Dispatch failure must NOT break the chat path (D2). Log and
            # move on; the after-chat data loss is recoverable from the
            # persisted message + meta.
            logger.error("CeleryDispatcher: send_task %s failed: %s", name, exc)
