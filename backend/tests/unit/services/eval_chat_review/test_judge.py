"""Tests for the L1 LLM judge (default OFF, cost-bounded)."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    MEDIUM,
    PASS,
    SUSPICIOUS,
    RuleResult,
    TurnPack,
)
from app.services.eval_chat_review.judge import L1Judge


def _llm() -> MockLLMClient:
    return MockLLMClient(role=LLMRole.JUDGE, model="mock-judge")


def _turn() -> TurnPack:
    return TurnPack(
        turn_id="t",
        index=0,
        user_message="你记得我老家在哪儿",
        assistant_reply="我记得在上海。",
        error=None,
        meta=None,
    )


@pytest.mark.asyncio
async def test_judge_skips_when_no_flagged_rules():
    judge = L1Judge(llm=_llm())
    out = await judge.judge_turn(
        turn=_turn(),
        l1_results=[RuleResult(id="ok", status=PASS, severity=HIGH)],
    )
    assert out is None


@pytest.mark.asyncio
async def test_judge_returns_verdict_for_flagged_turn():
    llm = _llm().queue_json(
        {
            "agrees": False,
            "corrected_status": "ok",
            "reason": "实际上回答正确",
        }
    )
    judge = L1Judge(llm=llm)
    out = await judge.judge_turn(
        turn=_turn(),
        l1_results=[
            RuleResult(id="reply_off_topic", status=SUSPICIOUS, severity=MEDIUM)
        ],
    )
    assert out is not None
    assert out.agrees is False
    assert out.corrected_status == "ok"


@pytest.mark.asyncio
async def test_judge_swallows_llm_error_returns_neutral_verdict():
    llm = _llm().queue(MockTurn(raises=LLMRateLimitError("429")))
    judge = L1Judge(llm=llm)
    out = await judge.judge_turn(
        turn=_turn(),
        l1_results=[RuleResult(id="x", status=FAIL, severity=HIGH)],
    )
    assert out is not None
    assert out.agrees is True
    assert out.corrected_status == "suspicious"


def test_apply_to_status_downgrades_when_judge_says_ok():
    judge = L1Judge(llm=_llm())
    verdict = type(
        "V", (), {"corrected_status": "ok", "agrees": False, "reason": ""}
    )()
    assert judge.apply_to_status(verdict, "suspicious") == "ok"
    assert judge.apply_to_status(verdict, "bad") == "ok"


def test_apply_to_status_upgrades_to_bad():
    judge = L1Judge(llm=_llm())
    verdict = type(
        "V", (), {"corrected_status": "bad", "agrees": True, "reason": ""}
    )()
    assert judge.apply_to_status(verdict, "good") == "bad"


def test_apply_to_status_no_change_when_verdict_none():
    judge = L1Judge(llm=_llm())
    assert judge.apply_to_status(None, "good") == "good"
