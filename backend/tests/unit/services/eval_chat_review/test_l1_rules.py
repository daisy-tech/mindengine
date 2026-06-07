"""L1 heuristic rules: per-rule pass/fail/skip behaviour."""

from __future__ import annotations

from app.services.eval_chat_review._types import (
    FAIL,
    PASS,
    SKIP,
    SUSPICIOUS,
    TurnPack,
)
from app.services.eval_chat_review.l1_rules import (
    ReviewContext,
    has_high_fail,
    has_suspicious,
    rule_correction_no_concrete_ack,
    rule_correction_persisted,
    rule_data_vs_activation_gap,
    rule_error_reply,
    rule_fabrication_under_challenge,
    rule_followup_overflow,
    rule_generic_reply,
    rule_personality_signature,
    rule_recall_for_challenge,
    rule_recall_for_self_summary,
    rule_reply_off_topic,
    run_l1_rules,
)


def _meta(intent: str = "casual", personality: str = "balanced", **kw):
    base = {
        "route": {"intent": intent, "personality": personality},
        "activated": [],
        "system_excerpt": "...",
        "section_keys": [],
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
        "assistant_reply": "嗨",
        "error": None,
        "meta": _meta(),
    }
    defaults.update(kw)
    return TurnPack(**defaults)


def _ctx(**kw) -> ReviewContext:
    return ReviewContext(**kw)


def test_error_reply_detects_marker():
    bad = _turn(assistant_reply="[出错] 网络故障")
    assert rule_error_reply(bad, None, _ctx()).status == FAIL


def test_error_reply_pass_when_clean():
    assert rule_error_reply(_turn(), None, _ctx()).status == PASS


def test_recall_for_challenge_skip_when_intent_not_challenge():
    assert (
        rule_recall_for_challenge(_turn(), None, _ctx()).status == SKIP
    )


def test_recall_for_challenge_pass_when_keywords_in_pool():
    turn = _turn(
        user_message="你记得我老家在哪",
        meta=_meta(
            intent="memory_challenge",
            activated=[{"source": "profile", "ref_id": "x", "excerpt": "老家=怀化"}],
        ),
    )
    assert rule_recall_for_challenge(turn, None, _ctx()).status == PASS


def test_recall_for_challenge_fail_when_keywords_missing():
    turn = _turn(
        user_message="你记得我妻子叫什么",
        meta=_meta(intent="memory_challenge", activated=[]),
    )
    assert rule_recall_for_challenge(turn, None, _ctx()).status == FAIL


def test_recall_for_self_summary_fail_with_empty_pool():
    turn = _turn(meta=_meta(intent="self_summary"))
    assert rule_recall_for_self_summary(turn, None, _ctx()).status == FAIL


def test_recall_for_self_summary_pass_when_activated():
    turn = _turn(
        meta=_meta(
            intent="self_summary",
            activated=[{"source": "profile", "ref_id": "x", "excerpt": "name=张三"}],
        )
    )
    assert rule_recall_for_self_summary(turn, None, _ctx()).status == PASS


def test_data_vs_activation_gap_skip_when_pools_empty():
    assert rule_data_vs_activation_gap(_turn(), None, _ctx()).status == SKIP


def test_data_vs_activation_gap_suspicious_when_pool_has_but_activated_zero():
    turn = _turn(
        meta=_meta(
            intent="memory_challenge",
            snapshot_stats={
                "profile_total": 0,
                "episodic_total": 5,
                "event_total": 0,
                "relationship_total": 0,
            },
        )
    )
    assert rule_data_vs_activation_gap(turn, None, _ctx()).status == SUSPICIOUS


def test_generic_reply_fail_when_too_short():
    bad = _turn(assistant_reply="嗯")
    assert rule_generic_reply(bad, None, _ctx()).status == FAIL


def test_generic_reply_suspicious_with_multiple_patterns():
    bad = _turn(assistant_reply="嗯，好的，明白")
    assert rule_generic_reply(bad, None, _ctx()).status == SUSPICIOUS


def test_generic_reply_pass_with_substantive_text():
    good = _turn(assistant_reply="今天和家人一起做了披萨，很惊喜地完成了。")
    assert rule_generic_reply(good, None, _ctx()).status == PASS


def test_reply_off_topic_skip_for_whitelist_intent():
    assert (
        rule_reply_off_topic(
            _turn(meta=_meta(intent="memory_challenge")), None, _ctx()
        ).status
        == SKIP
    )


def test_reply_off_topic_pass_when_overlap_present():
    turn = _turn(user_message="今天天气真好", assistant_reply="今天的确不错")
    assert rule_reply_off_topic(turn, None, _ctx()).status == PASS


def test_reply_off_topic_suspicious_when_no_overlap_and_no_question():
    turn = _turn(user_message="北京下雨了", assistant_reply="火锅很好吃。")
    assert rule_reply_off_topic(turn, None, _ctx()).status == SUSPICIOUS


def test_reply_off_topic_pass_with_followup_question():
    turn = _turn(user_message="北京下雨了", assistant_reply="还好吗？")
    assert rule_reply_off_topic(turn, None, _ctx()).status == PASS


def test_correction_persisted_skip_when_prev_not_correction():
    prev = _turn()
    cur = _turn()
    assert rule_correction_persisted(cur, prev, _ctx()).status == SKIP


def test_correction_persisted_fail_when_banned_re_emerges():
    prev = _turn(meta=_meta(intent="correction"))
    cur = _turn(assistant_reply="岳西的特产真的不错")
    ctx = _ctx(banned_entities={"岳西"})
    assert rule_correction_persisted(cur, prev, ctx).status == FAIL


def test_correction_persisted_pass_when_banned_absent():
    prev = _turn(meta=_meta(intent="correction"))
    cur = _turn(assistant_reply="谢谢你纠正我，我记下了怀宁。")
    ctx = _ctx(banned_entities={"岳西"})
    assert rule_correction_persisted(cur, prev, ctx).status == PASS


def test_correction_no_concrete_ack_pass_when_overlap_with_user_msg():
    turn = _turn(
        user_message="不对，是怀宁",
        assistant_reply="对，是怀宁，我记错了。",
        meta=_meta(intent="correction"),
    )
    assert rule_correction_no_concrete_ack(turn, None, _ctx()).status == PASS


def test_correction_no_concrete_ack_fail_without_overlap():
    turn = _turn(
        user_message="不对，是怀宁",
        assistant_reply="抱歉，已记录，下次不会再出现。",
        meta=_meta(intent="correction"),
    )
    assert rule_correction_no_concrete_ack(turn, None, _ctx()).status == FAIL


def test_fabrication_under_challenge_pass_when_entities_in_pool():
    turn = _turn(
        meta=_meta(
            intent="memory_challenge",
            activated=[{"source": "profile", "ref_id": "x", "excerpt": "李娜 妻子"}],
        ),
        assistant_reply="李娜在产品岗。",
    )
    ctx = _ctx(known_entities={"李娜"})
    assert rule_fabrication_under_challenge(turn, None, ctx).status == PASS


def test_fabrication_under_challenge_suspicious_for_off_pool_entity():
    turn = _turn(
        meta=_meta(intent="memory_challenge"),
        assistant_reply="我记得你提过赵琪。",
    )
    assert (
        rule_fabrication_under_challenge(turn, None, _ctx()).status == SUSPICIOUS
    )


def test_followup_overflow_pass_with_few_questions():
    turn = _turn(assistant_reply="今天怎么样？最近忙吗？")
    assert rule_followup_overflow(turn, None, _ctx()).status == PASS


def test_followup_overflow_suspicious_when_too_many_questions():
    turn = _turn(assistant_reply="A？B？C？D？")
    assert rule_followup_overflow(turn, None, _ctx()).status == SUSPICIOUS


def test_personality_signature_skip_for_knowledge_task():
    turn = _turn(meta=_meta(intent="knowledge_task"))
    assert rule_personality_signature(turn, None, _ctx()).status == SKIP


def test_personality_signature_fail_when_introvert_too_long():
    turn = _turn(
        meta=_meta(personality="introvert"),
        assistant_reply="多" * 200,
    )
    assert rule_personality_signature(turn, None, _ctx()).status == FAIL


def test_personality_signature_fail_when_introvert_ends_with_question():
    turn = _turn(
        meta=_meta(personality="introvert"),
        assistant_reply="挺好。你呢？",
    )
    assert rule_personality_signature(turn, None, _ctx()).status == FAIL


def test_personality_signature_pass_for_balanced_normal_reply():
    turn = _turn(
        meta=_meta(personality="balanced"),
        assistant_reply="今天确实不错，散步也很舒服。",
    )
    assert rule_personality_signature(turn, None, _ctx()).status == PASS


def test_run_l1_rules_returns_eleven():
    out = run_l1_rules(_turn(), None, _ctx())
    assert len(out) == 11


def test_run_l1_has_high_fail_detects_error_reply():
    bad = _turn(assistant_reply="[出错")
    out = run_l1_rules(bad, None, _ctx())
    assert has_high_fail(out)


def test_run_l1_has_suspicious_detects_followup_overflow():
    out = run_l1_rules(
        _turn(assistant_reply="A？B？C？D？"), None, _ctx()
    )
    assert has_suspicious(out)
