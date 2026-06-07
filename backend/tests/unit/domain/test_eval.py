"""Tests for domain.eval."""

from __future__ import annotations

from app.domain.eval import (
    EvalCase,
    EvalCheckResult,
    EvalResult,
    ExpectedAssertions,
    HistoryMsg,
    JudgeVerdict,
    ReviewSummary,
    RuleResult,
    TurnReview,
)
from app.domain.route import Intent, Personality


def test_eval_case_with_history() -> None:
    case = EvalCase(
        id="A-x01",
        personality=Personality.EXTROVERT,
        history=[
            HistoryMsg(role="user", content="我妻子叫张三"),
            HistoryMsg(role="assistant", content="记住了，张三是个好名字。"),
        ],
        user="她最近怎么样",
        expected=ExpectedAssertions(
            intents=[Intent.RELATIONSHIP_TOPIC],
            optional_intents=[Intent.EMOTIONAL_SUPPORT],
            must_activate_keywords=["张三", "妻子"],
            forbidden_phrases_in_reply=["听起来", "我能感受到"],
        ),
        tags=["relationship", "post_intro"],
    )
    assert case.expected.intents == [Intent.RELATIONSHIP_TOPIC]


def test_eval_result_round_trip() -> None:
    r = EvalResult(
        case_id="A-x01",
        checks=[
            EvalCheckResult(name="intent_match", passed=True),
            EvalCheckResult(name="forbidden_phrases", passed=False, detail="found 听起来"),
        ],
        passed=False,
        reply="听起来你心情不太好",
    )
    raw = r.model_dump_json()
    r2 = EvalResult.model_validate_json(raw)
    assert r2 == r


def test_turn_review_with_judge() -> None:
    review = TurnReview(
        l0_status="pass",
        l1_status="suspicious",
        final_status="ok",
        rules=[
            RuleResult(id="personality_signature", status="suspicious", severity="medium"),
        ],
        suggested_root_cause=["D"],
        judge=JudgeVerdict(agrees=False, corrected_status="pass", reason="模型已合规"),
    )
    assert review.judge is not None
    assert review.judge.agrees is False


def test_review_summary_defaults() -> None:
    s = ReviewSummary()
    assert s.schema_ == "eval_review_v1"
    assert s.evaluable_turns == 0
    # On-disk JSON uses key "schema" (alias) per doc 07 §3.6.
    dumped = s.model_dump(by_alias=True)
    assert dumped["schema"] == "eval_review_v1"
