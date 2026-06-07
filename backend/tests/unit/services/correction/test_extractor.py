"""Unit tests for correction.extractor."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.correction.extractor import CorrectionTargetExtractor


def _llm() -> MockLLMClient:
    return MockLLMClient(role=LLMRole.CORRECTION, model="mock-correction")


@pytest.mark.asyncio
async def test_extract_returns_targets_with_correct_field():
    llm = _llm().queue_json(
        {
            "targets": [
                {"ref": "岳西", "verb": "不是", "correct": "怀宁"},
            ]
        }
    )
    extractor = CorrectionTargetExtractor(llm=llm)
    out = await extractor.extract(
        user_correction="不对，是怀宁，不是岳西",
        ai_previous_reply="哦，岳西的米粉挺好吃。",
    )
    assert len(out) == 1
    assert out[0].ref == "岳西"
    assert out[0].correct == "怀宁"
    assert out[0].verb == "不是"


@pytest.mark.asyncio
async def test_extract_dedupes_repeat_refs():
    llm = _llm().queue_json(
        {
            "targets": [
                {"ref": "岳西", "verb": "不是", "correct": "怀宁"},
                {"ref": "岳西", "verb": "错了", "correct": None},
            ]
        }
    )
    extractor = CorrectionTargetExtractor(llm=llm)
    out = await extractor.extract(
        user_correction="不对", ai_previous_reply=""
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_extract_caps_at_max_targets():
    targets = [
        {"ref": f"e{i}", "verb": "不是", "correct": None} for i in range(8)
    ]
    llm = _llm().queue_json({"targets": targets})
    extractor = CorrectionTargetExtractor(llm=llm, max_targets=3)
    out = await extractor.extract(
        user_correction="纠正", ai_previous_reply="ok"
    )
    assert len(out) == 3


@pytest.mark.asyncio
async def test_extract_swallow_llm_error_returns_empty():
    llm = _llm().queue(MockTurn(raises=LLMRateLimitError("429")))
    extractor = CorrectionTargetExtractor(llm=llm)
    out = await extractor.extract(
        user_correction="不对", ai_previous_reply="ok"
    )
    assert out == []


@pytest.mark.asyncio
async def test_extract_empty_user_skips_llm_call():
    llm = _llm()  # no turns queued
    extractor = CorrectionTargetExtractor(llm=llm)
    out = await extractor.extract(
        user_correction="   ", ai_previous_reply="hi"
    )
    assert out == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_extract_normalizes_blank_correct_to_none():
    llm = _llm().queue_json(
        {"targets": [{"ref": "X", "verb": "不是", "correct": "   "}]}
    )
    extractor = CorrectionTargetExtractor(llm=llm)
    out = await extractor.extract(user_correction="不对", ai_previous_reply="")
    assert out[0].correct is None
