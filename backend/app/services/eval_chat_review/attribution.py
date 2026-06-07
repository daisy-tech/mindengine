"""Attribution codes (A-G).

Per docs/rebuild/07-Subsystem-Eval-Lab.md §3.4.

| code | name |
| ---- | ---- |
| A    | data_missing — recall_for_* |
| B    | route_misfire — intent err |
| C    | recall_no_activation — pool has, activated didn't |
| D    | persona_drift — personality_signature |
| E    | guide_drift — generic_reply, reply_off_topic |
| F    | data_pollution — correction_persisted |
| G    | hallucination — fabrication_under_challenge |

Aggregation returns a sorted list of (code, count) tuples (``root_cause_top``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum


class AttributionCode(StrEnum):
    A = "A"  # data_missing
    B = "B"  # route_misfire
    C = "C"  # recall_no_activation
    D = "D"  # persona_drift
    E = "E"  # guide_drift
    F = "F"  # data_pollution
    G = "G"  # hallucination


# Default mapping: rule_id -> attribution code. Rules are free to override
# this on a per-result basis (see RuleResult.attribution); this table is a
# fallback used when the rule didn't set one explicitly.
DEFAULT_ATTRIBUTION: dict[str, AttributionCode] = {
    "recall_for_challenge": AttributionCode.A,
    "recall_for_self_summary": AttributionCode.A,
    "data_vs_activation_gap": AttributionCode.C,
    "personality_signature": AttributionCode.D,
    "generic_reply": AttributionCode.E,
    "reply_off_topic": AttributionCode.E,
    "correction_persisted": AttributionCode.F,
    "correction_no_concrete_ack": AttributionCode.F,
    "fabrication_under_challenge": AttributionCode.G,
    "error_reply": AttributionCode.E,
    "followup_overflow": AttributionCode.E,
}


def aggregate_root_cause(
    items: Iterable[AttributionCode | str],
    *,
    top_k: int = 5,
) -> list[tuple[str, int]]:
    """Count attributions and return ``[[code, count], ...]`` sorted desc."""
    counter: Counter[str] = Counter()
    for it in items:
        if it is None:
            continue
        if isinstance(it, AttributionCode):
            counter[it.value] += 1
        else:
            counter[str(it)] += 1
    return [(k, v) for k, v in counter.most_common(top_k)]
