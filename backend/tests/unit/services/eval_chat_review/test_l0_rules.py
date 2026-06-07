"""L0 structural rules: per-rule pass/fail/skip + composite."""

from __future__ import annotations

from app.domain.prompt import SectionKey
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    PASS,
    SKIP,
    TurnPack,
)
from app.services.eval_chat_review.l0_rules import (
    has_high_fail,
    has_medium_fail,
    rule_activated_consistent_with_pools,
    rule_assistant_reply_nonempty,
    rule_background_section,
    rule_explicit_section,
    rule_intent_in_valid_set,
    rule_personality_in_valid_set,
    rule_prompt_meta_exists,
    rule_system_excerpt_nonempty,
    run_l0_rules,
)


def _meta(**overrides):
    base = {
        "route": {"intent": "casual", "personality": "balanced"},
        "system_excerpt": "你是个 AI",
        "section_keys": [SectionKey.BASE_PERSONA.value],
        "activated": [],
        "snapshot_stats": {
            "profile_total": 0,
            "episodic_total": 0,
            "event_total": 0,
            "relationship_total": 0,
        },
    }
    base.update(overrides)
    return base


def _turn(**kw) -> TurnPack:
    defaults: dict = {
        "turn_id": "t1",
        "index": 0,
        "user_message": "你好",
        "assistant_reply": "嗨",
        "error": None,
        "meta": _meta(),
    }
    defaults.update(kw)
    return TurnPack(**defaults)


def test_meta_exists_passes_with_dict():
    assert rule_prompt_meta_exists(_turn()).status == PASS


def test_meta_exists_fails_when_meta_missing():
    assert rule_prompt_meta_exists(_turn(meta=None)).status == FAIL


def test_assistant_reply_nonempty_pass_and_fail():
    assert rule_assistant_reply_nonempty(_turn()).status == PASS
    assert rule_assistant_reply_nonempty(_turn(assistant_reply="   ")).status == FAIL


def test_system_excerpt_nonempty_skips_when_no_meta():
    assert rule_system_excerpt_nonempty(_turn(meta=None)).status == SKIP


def test_system_excerpt_nonempty_fails_on_blank():
    assert (
        rule_system_excerpt_nonempty(_turn(meta=_meta(system_excerpt="   "))).status
        == FAIL
    )


def test_intent_in_valid_set_pass_and_fail():
    assert rule_intent_in_valid_set(_turn()).status == PASS
    bad = _turn(meta=_meta(route={"intent": "unknown", "personality": "balanced"}))
    assert rule_intent_in_valid_set(bad).status == FAIL


def test_personality_in_valid_set():
    assert rule_personality_in_valid_set(_turn()).status == PASS
    bad = _turn(meta=_meta(route={"intent": "casual", "personality": "weird"}))
    assert rule_personality_in_valid_set(bad).status == FAIL


def test_explicit_section_skip_when_no_activated():
    assert rule_explicit_section(_turn()).status == SKIP


def test_explicit_section_fail_when_activated_but_no_section():
    bad = _turn(
        meta=_meta(
            activated=[{"source": "episodic", "ref_id": "x"}],
            section_keys=[SectionKey.BASE_PERSONA.value],
        )
    )
    assert rule_explicit_section(bad).status == FAIL


def test_explicit_section_pass_when_present():
    good = _turn(
        meta=_meta(
            activated=[{"source": "episodic", "ref_id": "x"}],
            section_keys=[
                SectionKey.BASE_PERSONA.value,
                SectionKey.EXPLICIT_MEMORIES.value,
            ],
        )
    )
    assert rule_explicit_section(good).status == PASS


def test_background_section_skip_on_empty_pools():
    assert rule_background_section(_turn()).status == SKIP


def test_background_section_fail_when_pools_have_items_but_no_section():
    bad = _turn(
        meta=_meta(
            snapshot_stats={
                "profile_total": 1,
                "episodic_total": 5,
                "event_total": 0,
                "relationship_total": 0,
            },
            section_keys=[SectionKey.BASE_PERSONA.value],
        )
    )
    assert rule_background_section(bad).status == FAIL


def test_activated_consistency_pass_when_pools_zero_and_activated_zero():
    assert rule_activated_consistent_with_pools(_turn()).status == PASS


def test_activated_consistency_fail_when_pools_zero_but_activated_nonzero():
    bad = _turn(
        meta=_meta(
            activated=[{"source": "x", "ref_id": "1"}],
        )
    )
    assert rule_activated_consistent_with_pools(bad).status == FAIL


def test_run_l0_returns_8_results():
    results = run_l0_rules(_turn())
    assert len(results) == 8


def test_has_high_fail_detects_meta_missing():
    results = run_l0_rules(_turn(meta=None))
    assert has_high_fail(results)


def test_has_medium_fail_detects_inconsistent_pool():
    bad = _turn(meta=_meta(activated=[{"source": "x", "ref_id": "1"}]))
    results = run_l0_rules(bad)
    assert has_medium_fail(results)


def test_run_l0_does_not_raise_on_garbage_meta():
    bad = _turn(meta="not a dict")  # type: ignore[arg-type]
    results = run_l0_rules(bad)
    assert all(r.severity == HIGH or r.severity.value == "medium" for r in results)
