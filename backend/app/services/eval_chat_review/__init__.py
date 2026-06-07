"""Real-chat evaluation lab.

Per docs/rebuild/07-Subsystem-Eval-Lab.md.

Public surface (composed by ``reviewer.review_conversation``):

- ``TurnPack`` — per-turn input shape (user msg, reply, prompt_meta).
- ``L0Status`` / ``L1Status`` / ``RuleResult`` — rule output shape.
- ``review_conversation(...)`` — runs L0 + L1 + attribution + final_status.
- ``store`` — disk persistence for review packs (``eval_review_v1``).
"""

from app.services.eval_chat_review.attribution import (
    AttributionCode,
    aggregate_root_cause,
)
from app.services.eval_chat_review.l0_rules import L0_RULES, run_l0_rules
from app.services.eval_chat_review.l1_rules import L1_RULES, run_l1_rules
from app.services.eval_chat_review.reviewer import (
    L0Status,
    L1Status,
    ReviewContext,
    RuleResult,
    TurnPack,
    TurnReview,
    final_status,
    review_conversation,
)
from app.services.eval_chat_review.store import (
    list_stored_summaries,
    load_review,
    safe_id_segment,
    save_review,
)

__all__ = [
    "AttributionCode",
    "L0Status",
    "L0_RULES",
    "L1Status",
    "L1_RULES",
    "ReviewContext",
    "RuleResult",
    "TurnPack",
    "TurnReview",
    "aggregate_root_cause",
    "final_status",
    "list_stored_summaries",
    "load_review",
    "review_conversation",
    "run_l0_rules",
    "run_l1_rules",
    "safe_id_segment",
    "save_review",
]
