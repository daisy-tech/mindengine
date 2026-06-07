"""Per-turn + per-conversation review aggregator (L0 + L1 + attribution).

Per docs/rebuild/07-Subsystem-Eval-Lab.md §3.4 + §3.5 + §3.6.

The public entry point is ``review_conversation``: pass it a chronological
``Sequence[TurnPack]`` (and optionally a ``ReviewContext`` of known
entities + banned list) and you get back an ``eval_review_v1``-shaped
dict ready for ``store.save_review``.

The module re-exports the underlying types so api / tests don't have to
hop between submodules.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    PASS,
    SKIP,
    SUSPICIOUS,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    TurnPack,
)
from app.services.eval_chat_review.attribution import (
    DEFAULT_ATTRIBUTION,
    AttributionCode,
    aggregate_root_cause,
)
from app.services.eval_chat_review.l0_rules import (
    has_high_fail as l0_has_high_fail,
)
from app.services.eval_chat_review.l0_rules import (
    has_medium_fail as l0_has_medium_fail,
)
from app.services.eval_chat_review.l0_rules import (
    run_l0_rules,
)
from app.services.eval_chat_review.l1_rules import ReviewContext, run_l1_rules

# Re-exported for the package façade (eval_chat_review/__init__.py).
L0Status = RuleStatus
L1Status = RuleStatus

__all__ = [
    "L0Status",
    "L1Status",
    "ReviewContext",
    "RuleResult",
    "TurnPack",
    "TurnReview",
    "final_status",
    "review_conversation",
]


@dataclass(frozen=True)
class TurnReview:
    """Aggregated review for a single turn."""

    turn_id: str
    index: int
    l0_status: str
    l1_status: str
    final_status: str
    rules: list[RuleResult]
    attribution: list[str]


def _aggregate_status(
    results: Iterable[RuleResult], *, severities: tuple[RuleSeverity, ...] = ()
) -> str:
    """Reduce a rule list to a single status string (l0/l1 summary)."""
    rs = list(results)
    if severities:
        rs = [r for r in rs if r.severity in severities]
    if any(r.status == FAIL and r.severity == HIGH for r in rs):
        return FAIL.value
    if any(r.status == FAIL for r in rs):
        return FAIL.value
    if any(r.status == SUSPICIOUS for r in rs):
        return SUSPICIOUS.value
    if all(r.status == SKIP for r in rs):
        return SKIP.value
    return PASS.value


def final_status(l0: list[RuleResult], l1: list[RuleResult]) -> str:
    """Per docs §3.5 (with v0.97 fix: medium fail + L1 pass = ok)."""
    if all(r.status == SKIP for r in l0) and all(r.status == SKIP for r in l1):
        return "skip"
    if l0_has_high_fail(l0):
        return "bad"
    if any(r.severity == HIGH and r.status == FAIL for r in l1):
        return "bad"
    if l0_has_medium_fail(l0) and all(
        r.status in (PASS, SKIP) for r in l1
    ):
        return "ok"
    if any(r.status == SUSPICIOUS for r in l1):
        return "suspicious"
    return "good"


def _attribution_for(rule: RuleResult) -> str | None:
    if rule.attribution:
        return rule.attribution
    if rule.status in (FAIL, SUSPICIOUS):
        code = DEFAULT_ATTRIBUTION.get(rule.id)
        return code.value if code else None
    return None


def review_turn(
    turn: TurnPack,
    prev: TurnPack | None,
    ctx: ReviewContext | None = None,
) -> TurnReview:
    """Run L0 + L1 over one turn and aggregate."""
    l0 = run_l0_rules(turn)
    # When L0 high-fail: still run L1 for visibility, but note that the
    # final_status will already be "bad" so per-rule sus/fail won't change
    # the verdict — they'll just enrich the attribution.
    l1 = run_l1_rules(turn, prev, ctx)

    rules = list(l0) + list(l1)
    attribution = [a for a in (_attribution_for(r) for r in rules) if a]
    return TurnReview(
        turn_id=turn.turn_id,
        index=turn.index,
        l0_status=_aggregate_status(l0),
        l1_status=_aggregate_status(l1),
        final_status=final_status(l0, l1),
        rules=rules,
        attribution=attribution,
    )


def review_conversation(
    *,
    turns: Sequence[TurnPack],
    ctx: ReviewContext | None = None,
    schema: str = "eval_review_v1",
) -> dict[str, Any]:
    """Run reviews for every turn and emit the eval_review_v1 dict.

    The returned dict matches the §3.6 layout:

    {
      "schema": "eval_review_v1",
      "evaluated_at": <iso>,
      "review": {...counters + root_cause_top + rule_stats...},
      "turns": [{...turn-level rule + status...}],
    }
    """
    cx = ctx or ReviewContext()
    reviews: list[TurnReview] = []
    prev_turn: TurnPack | None = None
    for turn in turns:
        rv = review_turn(turn, prev_turn, cx)
        reviews.append(rv)
        prev_turn = turn

    counters: dict[str, int] = Counter()
    for rv in reviews:
        counters[f"final_{rv.final_status}"] += 1
        counters[f"l0_{rv.l0_status}"] += 1
        counters[f"l1_{rv.l1_status}"] += 1

    rule_stats: dict[str, dict[str, int]] = {}
    for rv in reviews:
        for r in rv.rules:
            slot = rule_stats.setdefault(r.id, {})
            slot[r.status.value] = slot.get(r.status.value, 0) + 1

    root_cause = aggregate_root_cause(
        a for rv in reviews for a in rv.attribution
    )
    final_ok_count = sum(
        1 for rv in reviews if rv.final_status in ("good", "ok")
    )
    evaluable = len(reviews)

    turn_payload: list[dict[str, Any]] = []
    for rv in reviews:
        turn_payload.append(
            {
                "turn_id": rv.turn_id,
                "index": rv.index,
                "l0_status": rv.l0_status,
                "l1_status": rv.l1_status,
                "final_status": rv.final_status,
                "attribution": rv.attribution,
                "rules": [
                    {
                        "id": r.id,
                        "status": r.status.value,
                        "severity": r.severity.value,
                        "detail": r.detail,
                        "attribution": r.attribution,
                    }
                    for r in rv.rules
                ],
            }
        )

    return {
        "schema": schema,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "review": {
            "evaluable_turns": evaluable,
            "final_ok_rate": (final_ok_count / evaluable) if evaluable else 0.0,
            "structure_pass_rate": (
                sum(1 for rv in reviews if rv.l0_status == PASS.value) / evaluable
                if evaluable
                else 0.0
            ),
            "counters": dict(counters),
            "rule_stats": rule_stats,
            "root_cause_top": [list(t) for t in root_cause],
        },
        "turns": turn_payload,
    }


def _ensure_attribution(code: str | None) -> AttributionCode | None:
    """Coerce a free-form attribution string to enum (defensive)."""
    if not code:
        return None
    try:
        return AttributionCode(code)
    except ValueError:
        return None
