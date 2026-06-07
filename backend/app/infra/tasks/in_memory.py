"""In-memory TaskDispatcher.

Used in M2 (no real workers wired yet) and in unit tests. Records every
dispatched task in-memory so tests can assert "after-chat hook fired".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.correction import CorrectionJudgement, CorrectionTarget


@dataclass(frozen=True)
class RecordedTask:
    name: str
    payload: dict[str, Any]


@dataclass
class InMemoryDispatcher:
    """Implements ``services.protocols.TaskDispatcher`` in-process."""

    recorded: list[RecordedTask] = field(default_factory=list)

    async def dispatch_after_chat(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        intent: str,
    ) -> None:
        self.recorded.append(
            RecordedTask(
                name="after_chat",
                payload={
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "intent": intent,
                },
            )
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
        self.recorded.append(
            RecordedTask(
                name="correction_cleanup",
                payload={
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "targets": [t.model_dump() for t in (targets or [])],
                    "judgements": [j.model_dump() for j in (judgements or [])],
                },
            )
        )
