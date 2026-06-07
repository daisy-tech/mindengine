"""Unit tests for correction.banned_extractor + clean_banned_entity."""

from __future__ import annotations

import pytest

from app.domain.correction import CorrectionTarget
from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.correction.banned_extractor import (
    BannedExtractor,
    clean_banned_entity,
    dedup_banned,
)


def _llm() -> MockLLMClient:
    return MockLLMClient(role=LLMRole.EXTRACT, model="mock-banned")


def test_clean_banned_entity_strips_whitespace_and_punct():
    assert clean_banned_entity(" 岳西 ") == "岳西"
    assert clean_banned_entity("a-b") == "ab"
    assert clean_banned_entity("阿里巴巴") == "阿里巴巴"


def test_clean_banned_entity_rejects_too_long():
    assert clean_banned_entity("a" * 9, max_len=8) is None


def test_clean_banned_entity_rejects_only_punctuation():
    assert clean_banned_entity("!!!") is None
    assert clean_banned_entity("") is None


def test_dedup_banned_normalizes_case_lower():
    out = dedup_banned(["A", "a", "B"])
    assert sorted(out) == ["A", "B"]  # first-seen wins


@pytest.mark.asyncio
async def test_extract_filters_correct_value_out():
    llm = _llm().queue_json({"entities": ["岳西", "怀宁"]})
    targets = [CorrectionTarget(ref="岳西", verb="不是", correct="怀宁")]
    out = await BannedExtractor(llm=llm).extract(
        user_correction="不对，是怀宁，不是岳西", targets=targets
    )
    assert out == ["岳西"]


@pytest.mark.asyncio
async def test_extract_falls_back_to_target_refs_on_llm_error():
    llm = _llm().queue(MockTurn(raises=LLMRateLimitError("429")))
    targets = [CorrectionTarget(ref="岳西", verb="不对")]
    out = await BannedExtractor(llm=llm).extract(
        user_correction="不对", targets=targets
    )
    assert out == ["岳西"]


@pytest.mark.asyncio
async def test_extract_caps_at_max_entities():
    llm = _llm().queue_json({"entities": list("ABCDEFGH")})
    targets = [CorrectionTarget(ref="X", verb="不是")]
    out = await BannedExtractor(llm=llm, max_entities=3).extract(
        user_correction="不对", targets=targets
    )
    assert len(out) == 3


@pytest.mark.asyncio
async def test_extract_empty_targets_returns_empty():
    llm = _llm()  # no turns queued
    out = await BannedExtractor(llm=llm).extract(
        user_correction="不对", targets=[]
    )
    assert out == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_extract_drops_too_long_entities_per_clean_rule():
    llm = _llm().queue_json({"entities": ["short", "x" * 20]})
    targets = [CorrectionTarget(ref="x", verb="不")]
    out = await BannedExtractor(llm=llm, max_len=8).extract(
        user_correction="不对", targets=targets
    )
    assert out == ["short"]
