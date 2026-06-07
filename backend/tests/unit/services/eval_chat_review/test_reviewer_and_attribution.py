"""Tests for the reviewer aggregator + attribution helper."""

from __future__ import annotations

from app.domain.prompt import SectionKey
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    MEDIUM,
    PASS,
    SKIP,
    RuleResult,
    TurnPack,
)
from app.services.eval_chat_review.attribution import (
    AttributionCode,
    aggregate_root_cause,
)
from app.services.eval_chat_review.l1_rules import ReviewContext
from app.services.eval_chat_review.reviewer import (
    final_status,
    review_conversation,
    review_turn,
)


def _meta(intent: str = "casual", personality: str = "balanced", **kw):
    base = {
        "route": {"intent": intent, "personality": personality},
        "activated": [],
        "system_excerpt": "你是 AI",
        "section_keys": [SectionKey.BASE_PERSONA.value],
        "snapshot_stats": {
            "profile_total": 0,
            "episodic_total": 0,
            "event_total": 0,
            "relationship_total": 0,
        },
    }
    base.update(kw)
    return base


def _turn(**kw) -> TurnPack:
    defaults: dict = {
        "turn_id": "t",
        "index": 0,
        "user_message": "你好",
        "assistant_reply": "嗨，今天怎么样？",
        "error": None,
        "meta": _meta(),
    }
    defaults.update(kw)
    return TurnPack(**defaults)


def test_aggregate_root_cause_orders_desc():
    out = aggregate_root_cause(["D", "D", "E", "A", AttributionCode.D])
    codes = [c for c, _ in out]
    assert codes[0] == "D"
    assert ("E", 1) in out


def test_final_status_high_l0_fail_yields_bad():
    l0 = [
        RuleResult(id="prompt_meta.exists", status=FAIL, severity=HIGH),
    ]
    assert final_status(l0, []) == "bad"


def test_final_status_medium_l0_fail_with_clean_l1_yields_ok():
    l0 = [RuleResult(id="m", status=FAIL, severity=MEDIUM)]
    l1 = [RuleResult(id="ok", status=PASS, severity=HIGH)]
    assert final_status(l0, l1) == "ok"


def test_final_status_l1_high_fail_yields_bad():
    l0 = [RuleResult(id="ok", status=PASS, severity=HIGH)]
    l1 = [RuleResult(id="error_reply", status=FAIL, severity=HIGH)]
    assert final_status(l0, l1) == "bad"


def test_final_status_l1_suspicious_yields_suspicious():
    l0 = [RuleResult(id="ok", status=PASS, severity=HIGH)]
    l1 = [
        RuleResult(id="reply_off_topic", status="suspicious", severity=MEDIUM)
    ]
    assert final_status(l0, l1) == "suspicious"


def test_final_status_all_skip_yields_skip():
    l0 = [RuleResult(id="x", status=SKIP, severity=HIGH)]
    l1 = [RuleResult(id="y", status=SKIP, severity=MEDIUM)]
    assert final_status(l0, l1) == "skip"


def test_final_status_clean_yields_good():
    l0 = [RuleResult(id="ok", status=PASS, severity=HIGH)]
    l1 = [RuleResult(id="ok", status=PASS, severity=HIGH)]
    assert final_status(l0, l1) == "good"


def test_review_turn_attribution_includes_default_codes():
    bad = _turn(assistant_reply="[出错")
    rv = review_turn(bad, None)
    assert "E" in rv.attribution


def test_review_conversation_emits_review_block():
    pack = _turn()
    pack_bad = _turn(turn_id="t2", index=1, assistant_reply="[出错")
    out = review_conversation(turns=[pack, pack_bad])
    assert out["schema"] == "eval_review_v1"
    block = out["review"]
    assert block["evaluable_turns"] == 2
    assert "final_bad" in block["counters"]
    assert any(turn["final_status"] == "bad" for turn in out["turns"])


def test_review_conversation_propagates_correction_persisted_via_prev():
    prev = _turn(turn_id="prev", meta=_meta(intent="correction"))
    cur = _turn(
        turn_id="cur",
        assistant_reply="岳西的特产真的不错",
        index=1,
    )
    ctx = ReviewContext(banned_entities={"岳西"})
    out = review_conversation(turns=[prev, cur], ctx=ctx)
    final = {t["turn_id"]: t["final_status"] for t in out["turns"]}
    assert final["cur"] == "bad"


def test_review_conversation_zero_turns_yields_safe_defaults():
    out = review_conversation(turns=[])
    assert out["review"]["evaluable_turns"] == 0
    assert out["review"]["final_ok_rate"] == 0.0
    assert out["turns"] == []
