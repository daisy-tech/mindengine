"""Unit tests for correction.judge."""

from __future__ import annotations

import pytest

from app.domain.correction import CorrectionCandidate
from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMTimeoutError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.correction.judge import CorrectionJudge


def _llm() -> MockLLMClient:
    return MockLLMClient(role=LLMRole.CORRECTION, model="mock-judge")


def _cand() -> CorrectionCandidate:
    return CorrectionCandidate(source="episodic", ref_id="m1", text="老家在岳西")


@pytest.mark.asyncio
async def test_judge_high_confidence_deprecate_passes_through():
    llm = _llm().queue_json(
        {"action": "deprecate", "confidence": 0.95, "reason": "明确否定"}
    )
    judge = CorrectionJudge(llm=llm, confidence_threshold=0.7)
    out = await judge.judge(
        user_correction="不对，是怀宁",
        ai_previous_reply="岳西很好",
        candidate=_cand(),
    )
    assert out.judgement.action == "deprecate"
    assert out.judgement.confidence == 0.95


@pytest.mark.asyncio
async def test_judge_low_confidence_coerced_to_audit_only():
    llm = _llm().queue_json(
        {"action": "deprecate", "confidence": 0.5, "reason": "不确定"}
    )
    judge = CorrectionJudge(llm=llm, confidence_threshold=0.7)
    out = await judge.judge(
        user_correction="不太对", ai_previous_reply="岳西", candidate=_cand()
    )
    assert out.judgement.action == "audit_only"


@pytest.mark.asyncio
async def test_judge_update_without_new_text_falls_back_to_deprecate():
    llm = _llm().queue_json(
        {
            "action": "update",
            "confidence": 0.9,
            "reason": "替换",
            "new_text": "  ",
        }
    )
    judge = CorrectionJudge(llm=llm)
    out = await judge.judge(
        user_correction="不对", ai_previous_reply="岳西", candidate=_cand()
    )
    assert out.judgement.action == "deprecate"
    assert out.judgement.new_text is None


@pytest.mark.asyncio
async def test_judge_update_passes_new_text_through():
    llm = _llm().queue_json(
        {
            "action": "update",
            "confidence": 0.85,
            "reason": "替换",
            "new_text": "怀宁",
        }
    )
    judge = CorrectionJudge(llm=llm)
    out = await judge.judge(
        user_correction="不对，是怀宁",
        ai_previous_reply="岳西",
        candidate=_cand(),
    )
    assert out.judgement.action == "update"
    assert out.judgement.new_text == "怀宁"


@pytest.mark.asyncio
async def test_judge_swallows_llm_timeout_returns_audit_only():
    llm = _llm().queue(MockTurn(raises=LLMTimeoutError("slow")))
    judge = CorrectionJudge(llm=llm)
    out = await judge.judge(
        user_correction="不对", ai_previous_reply="x", candidate=_cand()
    )
    assert out.judgement.action == "audit_only"
    assert out.judgement.confidence == 0.0


@pytest.mark.asyncio
async def test_judge_truncates_long_new_text():
    llm = _llm().queue_json(
        {
            "action": "update",
            "confidence": 0.9,
            "reason": "x",
            "new_text": "怀宁" * 100,
        }
    )
    judge = CorrectionJudge(llm=llm, max_new_text_chars=20)
    out = await judge.judge(
        user_correction="不对", ai_previous_reply="x", candidate=_cand()
    )
    assert out.judgement.new_text is not None
    assert len(out.judgement.new_text) <= 20
