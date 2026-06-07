"""ContractGuard tests (D3 post-processing)."""

from __future__ import annotations

from app.domain.route import Personality
from app.services.contract_guard import ContractGuard


def test_passthrough_when_below_cap_and_no_question() -> None:
    g = ContractGuard()
    res = g.enforce("好的，我记下了。", Personality.INTROVERT)
    assert res.text == "好的，我记下了。"
    assert res.enforcement.truncated is False
    assert res.enforcement.removed_question is False
    assert res.enforcement.raw_char_count == len("好的，我记下了。")
    assert res.enforcement.final_char_count == len("好的，我记下了。")


def test_introvert_strips_trailing_question() -> None:
    g = ContractGuard()
    res = g.enforce("我懂你的感受。要聊聊吗？", Personality.INTROVERT)
    assert res.text == "我懂你的感受。"
    assert res.enforcement.removed_question is True


def test_balanced_keeps_question() -> None:
    g = ContractGuard()
    text = "听起来挺累的。最近怎么调节？"
    res = g.enforce(text, Personality.BALANCED)
    assert res.text == text
    assert res.enforcement.removed_question is False


def test_truncates_at_sentence_boundary_when_exceeding_cap() -> None:
    g = ContractGuard()
    # introvert cap = 80
    text = "句一。" * 30  # 90 chars: "句一。句一。..." 30 times
    res = g.enforce(text, Personality.INTROVERT)
    assert res.enforcement.truncated is True
    assert len(res.text) <= 80
    assert res.text.endswith("。")


def test_truncates_with_ellipsis_when_no_boundary_fits() -> None:
    g = ContractGuard()
    # 80 chars no terminator — must fall back to hard cut + "…"
    text = "字" * 100
    res = g.enforce(text, Personality.INTROVERT)
    assert res.enforcement.truncated is True
    assert res.text.endswith("…")


def test_handles_empty_input() -> None:
    g = ContractGuard()
    res = g.enforce("", Personality.BALANCED)
    assert res.text == ""
    assert res.enforcement.raw_char_count == 0


def test_only_question_introvert_returns_empty() -> None:
    # If the entire reply is a single question for an introvert, we'd
    # rather emit nothing than emit a half-thought.
    g = ContractGuard()
    res = g.enforce("怎么了？", Personality.INTROVERT)
    assert res.text == ""
    assert res.enforcement.removed_question is True


def test_extrovert_caps_at_200() -> None:
    g = ContractGuard()
    text = "句子。" * 80  # 240 chars
    res = g.enforce(text, Personality.EXTROVERT)
    assert res.enforcement.truncated is True
    assert len(res.text) <= 200


def test_question_strip_records_both_flags_when_also_truncating() -> None:
    g = ContractGuard()
    # Introvert cap = 80. Build text > 80 chars ending in "？".
    text = ("我明白你的感受，慢慢来。" * 8).rstrip("。") + "？"
    assert len(text) > 80
    res = g.enforce(text, Personality.INTROVERT)
    assert res.enforcement.removed_question is True
    assert len(res.text) <= 80
