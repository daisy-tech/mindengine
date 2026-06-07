"""Synthetic case runner.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.3.

The runner takes a ``Protocol`` describing "how to run one case" + the
case itself, and produces an ``EvalResult`` of named checks.

Splitting the "actually drive the chat" from "score the result" lets us:
- Unit-test ``check_case`` deterministically with hand-built outcomes.
- Plug in a real orchestrator-based runner in production (under the
  EVAL_USER_ID) without dragging FastAPI into the runner module.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.domain.eval import EvalCase, EvalCheckResult, EvalResult
from app.domain.route import Intent


@dataclass(frozen=True)
class SyntheticTurnOutcome:
    """What a single synthetic chat turn looks like for scoring.

    The runner Protocol returns this; the case scorer reads it.
    """

    reply: str
    intent: str
    intent_source: str = ""
    system_excerpt: str = ""
    activated_keywords: tuple[str, ...] = ()
    error: str | None = None
    elapsed_ms: int = 0


@runtime_checkable
class SyntheticCaseRunner(Protocol):
    """Interface implemented by anything that can run one EvalCase."""

    async def run(self, case: EvalCase) -> SyntheticTurnOutcome: ...


@dataclass(frozen=True)
class SyntheticRunResult:
    """One case's outcome after scoring."""

    case: EvalCase
    outcome: SyntheticTurnOutcome
    result: EvalResult


# ─── case scoring ─────────────────────────────────────────────────


def _check_intent(
    case: EvalCase, outcome: SyntheticTurnOutcome
) -> EvalCheckResult:
    expected = {i.value for i in case.expected.intents}
    optional = {i.value for i in case.expected.optional_intents}
    if not expected:
        return EvalCheckResult(name="intent", passed=True, detail="no intent expected")
    if outcome.intent in expected or outcome.intent in optional:
        return EvalCheckResult(name="intent", passed=True, detail=outcome.intent)
    return EvalCheckResult(
        name="intent",
        passed=False,
        detail=f"got {outcome.intent!r}, expected {sorted(expected)!r}",
    )


def _check_must_activate(
    case: EvalCase, outcome: SyntheticTurnOutcome
) -> EvalCheckResult:
    needed = list(case.expected.must_activate_keywords)
    if not needed:
        return EvalCheckResult(name="must_activate", passed=True)
    blob = (
        " ".join(outcome.activated_keywords).lower()
        + " "
        + outcome.system_excerpt.lower()
    )
    missing = [k for k in needed if k.lower() not in blob]
    if not missing:
        return EvalCheckResult(name="must_activate", passed=True, detail=",".join(needed))
    return EvalCheckResult(
        name="must_activate",
        passed=False,
        detail=f"missing {missing!r}",
    )


def _check_must_contain(
    case: EvalCase, outcome: SyntheticTurnOutcome
) -> EvalCheckResult:
    expected = list(case.expected.must_contain)
    if not expected:
        return EvalCheckResult(name="must_contain", passed=True)
    reply = (outcome.reply or "")
    missing = [k for k in expected if k not in reply]
    if not missing:
        return EvalCheckResult(name="must_contain", passed=True, detail=",".join(expected))
    return EvalCheckResult(
        name="must_contain",
        passed=False,
        detail=f"reply missing {missing!r}",
    )


def _check_forbidden_in_reply(
    case: EvalCase, outcome: SyntheticTurnOutcome
) -> EvalCheckResult:
    forbidden = list(case.expected.forbidden_phrases_in_reply) + list(
        case.expected.forbidden
    )
    if not forbidden:
        return EvalCheckResult(name="forbidden_in_reply", passed=True)
    reply = (outcome.reply or "")
    leaked = [p for p in forbidden if p and p in reply]
    if not leaked:
        return EvalCheckResult(name="forbidden_in_reply", passed=True)
    return EvalCheckResult(
        name="forbidden_in_reply",
        passed=False,
        detail=f"leaked {leaked!r}",
    )


def _check_forbidden_in_system(
    case: EvalCase, outcome: SyntheticTurnOutcome
) -> EvalCheckResult:
    forbidden = list(case.expected.forbidden_phrases_in_system)
    if not forbidden:
        return EvalCheckResult(name="forbidden_in_system", passed=True)
    sys = outcome.system_excerpt or ""
    leaked = [p for p in forbidden if p and p in sys]
    if not leaked:
        return EvalCheckResult(name="forbidden_in_system", passed=True)
    return EvalCheckResult(
        name="forbidden_in_system",
        passed=False,
        detail=f"system leaked {leaked!r}",
    )


def check_case(case: EvalCase, outcome: SyntheticTurnOutcome) -> EvalResult:
    """Run the standard 5-check suite over one case + outcome."""
    checks: list[EvalCheckResult] = [
        _check_intent(case, outcome),
        _check_must_activate(case, outcome),
        _check_must_contain(case, outcome),
        _check_forbidden_in_reply(case, outcome),
        _check_forbidden_in_system(case, outcome),
    ]
    if outcome.error:
        checks.append(
            EvalCheckResult(
                name="no_error", passed=False, detail=f"chat error: {outcome.error}"
            )
        )
    else:
        checks.append(EvalCheckResult(name="no_error", passed=True))

    return EvalResult(
        case_id=case.id,
        checks=checks,
        passed=all(c.passed for c in checks),
        reply=outcome.reply,
    )


# ─── batch runner ─────────────────────────────────────────────────


@dataclass
class SyntheticBatch:
    """Results of one batch run, ready for ``reporter.build_report``."""

    started_at: float
    finished_at: float = 0.0
    items: list[SyntheticRunResult] = field(default_factory=list)


async def run_cases(
    runner: SyntheticCaseRunner, cases: Iterable[EvalCase]
) -> SyntheticBatch:
    """Run every case sequentially. Failures inside a single runner are
    converted to error checks; we never abort the whole batch."""
    batch = SyntheticBatch(started_at=time.time())
    for case in cases:
        try:
            outcome = await runner.run(case)
        except Exception as exc:  # pragma: no cover — defensive
            outcome = SyntheticTurnOutcome(
                reply="", intent="", error=f"runner_error: {exc!r}"
            )
        result = check_case(case, outcome)
        batch.items.append(SyntheticRunResult(case=case, outcome=outcome, result=result))
    batch.finished_at = time.time()
    return batch


def confusion_matrix(
    items: Sequence[SyntheticRunResult],
) -> dict[str, dict[str, int]]:
    """Per-intent confusion matrix (expected → actual counts)."""
    cm: dict[str, dict[str, int]] = {}
    for it in items:
        expected_intents = (
            [i.value for i in it.case.expected.intents] or [Intent.CASUAL.value]
        )
        for ex in expected_intents:
            row = cm.setdefault(ex, {})
            actual = it.outcome.intent or "unknown"
            row[actual] = row.get(actual, 0) + 1
    return cm
