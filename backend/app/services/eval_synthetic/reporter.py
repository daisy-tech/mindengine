"""Aggregate a SyntheticBatch into a §2.4 report dict.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.4. Output is JSON-friendly
so it can be written next to the review packs (or returned via API).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.eval_synthetic.runner import SyntheticBatch, confusion_matrix


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def build_report(
    batch: SyntheticBatch,
    *,
    run_id: str,
    run_type: str = "smoke",
) -> dict[str, Any]:
    total = len(batch.items)
    passed = sum(1 for it in batch.items if it.result.passed)
    pass_rate = (passed / total) if total else 0.0

    cases_payload: list[dict[str, Any]] = []
    for it in batch.items:
        cases_payload.append(
            {
                "id": it.case.id,
                "personality": it.case.personality.value,
                "tags": list(it.case.tags),
                "passed": it.result.passed,
                "intent_actual": it.outcome.intent,
                "intent_source": it.outcome.intent_source,
                "reply": it.outcome.reply,
                "checks": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "detail": c.detail,
                    }
                    for c in it.result.checks
                ],
                "elapsed_ms": it.outcome.elapsed_ms,
                "error": it.outcome.error,
            }
        )

    return {
        "schema": "synthetic_run_v1",
        "run_id": run_id,
        "run_type": run_type,
        "started_at": _fmt_ts(batch.started_at),
        "finished_at": _fmt_ts(batch.finished_at or batch.started_at),
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "intent_confusion_matrix": confusion_matrix(batch.items),
        "cases": cases_payload,
    }
