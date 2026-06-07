"""CeleryDispatcher — verifies it forwards through ``send_task``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.domain.correction import CorrectionTarget
from app.infra.tasks.celery_dispatcher import CeleryDispatcher


@dataclass
class _FakeCelery:
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    raise_on_send: Exception | None = None

    def send_task(self, name: str, *, kwargs: dict[str, Any]) -> str:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append((name, kwargs))
        return "task-id-stub"


@pytest.mark.asyncio
async def test_dispatch_after_chat_publishes_correct_task():
    celery = _FakeCelery()
    disp = CeleryDispatcher(celery_app=celery)
    await disp.dispatch_after_chat(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        intent="casual",
    )
    assert len(celery.sent) == 1
    name, kwargs = celery.sent[0]
    assert name == "memory.after_chat"
    assert kwargs == {
        "user_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
        "intent": "casual",
    }


@pytest.mark.asyncio
async def test_dispatch_correction_publishes_correct_task():
    """The worker re-derives targets from message history, so the
    dispatcher only forwards the (user, conversation, message) triple.
    ``targets`` / ``judgements`` remain in the protocol for forward
    compatibility but must NOT be put on the wire.
    """
    celery = _FakeCelery()
    disp = CeleryDispatcher(celery_app=celery)
    await disp.dispatch_correction_cleanup(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        targets=[CorrectionTarget(ref="岳西", verb="不是", correct="怀宁")],
    )
    assert len(celery.sent) == 1
    name, kwargs = celery.sent[0]
    assert name == "correction.cleanup"
    assert kwargs == {
        "user_id": "u1",
        "conversation_id": "c1",
        "message_id": "m1",
    }


@pytest.mark.asyncio
async def test_dispatch_swallows_broker_errors():
    celery = _FakeCelery(raise_on_send=RuntimeError("broker down"))
    disp = CeleryDispatcher(celery_app=celery)
    # Must not raise — chat path tolerance is the entire reason this exists.
    await disp.dispatch_after_chat(
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
        intent="casual",
    )
