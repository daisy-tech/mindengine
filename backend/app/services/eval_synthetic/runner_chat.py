"""Chat-backed synthetic runner for the eval lab.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.3.

Drives a single :class:`~app.domain.eval.EvalCase` end-to-end through the
live ``MemoryRouter → PromptComposer → chat LLM`` stack, but persists
nothing. The result is a :class:`~app.services.eval_synthetic.runner.SyntheticTurnOutcome`
that the standard ``check_case`` scorer can grade.

Design notes
------------

- **Memory layers stay empty.** Synthetic cases are self-contained: their
  facts live in ``case.history``, not in the persisted 4-layer memory of
  any real user. We pass an empty ``MemoryContext()`` to the composer.
- **History → LLM messages.** ``case.history`` is forwarded as the
  conversation prefix so the chat LLM has the same context the user
  would have seen. This is what makes ``must_contain`` checks meaningful
  for recall cases.
- **Activated keywords surfacing.** Because we don't seed memory layers,
  ``pack.activated_items`` is empty. The standard ``must_activate``
  scorer reads from ``activated_keywords + system_excerpt``; we surface
  the joined history (and the reply) as activated keywords so cases that
  expect "the model used the prior fact" still have something to match
  against. If you need stricter semantics (memory was actually retrieved
  from the 4 layers), seed the EVAL_USER_ID via ``/api/eval/seed-persona``
  and write cases that target the seeded persona.
- **No persistence, no dispatch.** Calling this runner does not write to
  the messages table and does not enqueue celery tasks; it leaves the
  EVAL_USER_ID's data exactly as it found it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.domain.eval import EvalCase
from app.domain.memory import MemoryContext
from app.services.eval_synthetic.runner import SyntheticTurnOutcome
from app.services.memory_router import MemoryRouter
from app.services.prompt_composer import PromptComposer
from app.services.protocols import LLMClient

_SYSTEM_EXCERPT_CHARS = 1000


@dataclass
class ChatBackedSyntheticRunner:
    """Implements the ``SyntheticCaseRunner`` Protocol.

    Wired into ``app.state.synthetic_runner`` by the FastAPI lifespan;
    the eval HTTP layer (``api.eval.start_synthetic``) reads it from
    there for every batch.
    """

    router: MemoryRouter
    composer: PromptComposer
    chat_llm: LLMClient
    eval_user_id: str
    chat_temperature: float = 0.7

    async def run(self, case: EvalCase) -> SyntheticTurnOutcome:
        start = time.monotonic()
        history_msgs: list[dict[str, str]] = [
            {"role": h.role, "content": h.content} for h in case.history
        ]
        try:
            route = await self.router.route(
                user_id=self.eval_user_id,
                message=case.user,
                history=history_msgs,
                personality=case.personality,
            )
            pack = self.composer.compose(route=route, ctx=MemoryContext())
            llm_messages = [*history_msgs, {"role": "user", "content": case.user}]
            stream = await self.chat_llm.stream(
                pack.system,
                llm_messages,
                temperature=self.chat_temperature,
            )
            chunks: list[str] = []
            async for chunk in stream:
                chunks.append(chunk)
            reply = "".join(chunks).strip()
        except Exception as exc:  # noqa: BLE001 — runner-level failures are reported, not raised
            return SyntheticTurnOutcome(
                reply="",
                intent="",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=_elapsed_ms(start),
            )

        # Surface history + reply for the must_activate check (see module
        # docstring). Tuple shape per SyntheticTurnOutcome.activated_keywords.
        history_blob = " ".join(h["content"] for h in history_msgs)
        activated: tuple[str, ...] = tuple(s for s in (history_blob, reply) if s)

        return SyntheticTurnOutcome(
            reply=reply,
            intent=route.intent.value,
            intent_source=route.intent_source.value,
            system_excerpt=pack.system[:_SYSTEM_EXCERPT_CHARS],
            activated_keywords=activated,
            error=None,
            elapsed_ms=_elapsed_ms(start),
        )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
