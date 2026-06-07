"""Evaluation lab domain types: synthetic cases and chat-review rule results.

Per docs/rebuild/07-Subsystem-Eval-Lab.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.route import Intent, Personality

# ─────────────────────────────────────────────────────── synthetic ──


class HistoryMsg(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class ExpectedAssertions(BaseModel):
    model_config = ConfigDict(frozen=True)

    intents: list[Intent] = Field(default_factory=list)
    optional_intents: list[Intent] = Field(default_factory=list)
    must_activate_keywords: list[str] = Field(default_factory=list)
    forbidden_phrases_in_reply: list[str] = Field(default_factory=list)
    forbidden_phrases_in_system: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    personality: Personality
    history: list[HistoryMsg] = Field(default_factory=list)
    user: str
    expected: ExpectedAssertions = Field(default_factory=ExpectedAssertions)
    tags: list[str] = Field(default_factory=list)


class EvalCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    detail: str = ""


class EvalResult(BaseModel):
    model_config = ConfigDict(frozen=False)

    case_id: str
    checks: list[EvalCheckResult] = Field(default_factory=list)
    passed: bool = False
    reply: str = ""


# ─────────────────────────────────────────────────────── chat review ──


RuleStatus = Literal["pass", "fail", "suspicious", "skip"]
RuleSeverity = Literal["high", "medium", "low"]
FinalStatus = Literal["good", "ok", "suspicious", "bad", "skip"]
AttributionCode = Literal["A", "B", "C", "D", "E", "F", "G"]


class RuleResult(BaseModel):
    """Output of one L0 / L1 rule evaluation (doc 07 §3.3)."""

    model_config = ConfigDict(frozen=False)

    id: str
    status: RuleStatus
    severity: RuleSeverity
    detail: str = ""
    attribution: AttributionCode | None = None


class JudgeVerdict(BaseModel):
    """Optional small-model judge re-check on flagged turns (doc 07 §3.3a / D5)."""

    model_config = ConfigDict(frozen=True)

    agrees: bool
    corrected_status: RuleStatus | None = None
    reason: str = ""


class TurnReview(BaseModel):
    model_config = ConfigDict(frozen=False)

    l0_status: RuleStatus
    l1_status: RuleStatus
    final_status: FinalStatus
    rules: list[RuleResult] = Field(default_factory=list)
    suggested_root_cause: list[AttributionCode] = Field(default_factory=list)
    snapshot_status: Literal["at_turn", "post", "missing"] = "at_turn"
    judge: JudgeVerdict | None = None


class ReviewSummary(BaseModel):
    """Aggregated counters across turns (doc 07 §3.6).

    The on-disk JSON key is `"schema"` (per the design doc); we keep the
    Python attribute as `schema_` because plain `schema` shadows
    `BaseModel.schema()`. `populate_by_name=True` lets callers pass either.
    """

    model_config = ConfigDict(frozen=False, populate_by_name=True)

    schema_: str = Field(default="eval_review_v1", alias="schema")
    evaluable_turns: int = 0
    structure_pass_rate: float = 0.0
    final_ok_rate: float = 0.0
    counters: dict[str, int] = Field(default_factory=dict)
    rule_stats: dict[str, dict[str, int]] = Field(default_factory=dict)
    root_cause_top: list[tuple[AttributionCode, int]] = Field(default_factory=list)
    evaluated_at: datetime | None = None
