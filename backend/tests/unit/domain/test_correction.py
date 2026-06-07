"""Tests for domain.correction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.correction import (
    BannedEntity,
    CorrectionCandidate,
    CorrectionJudgement,
    CorrectionTarget,
    MemoryDeprecation,
)


class TestBannedEntity:
    def test_strips_whitespace(self) -> None:
        # Lesson 5.2 + doc 06 §6.4: store cleaned, deduped entities.
        b = BannedEntity(user_id="u1", entity="  岳西  ")
        assert b.entity == "岳西"

    def test_empty_after_strip_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BannedEntity(user_id="u1", entity="   ")

    def test_too_long_rejected(self) -> None:
        # ≤ 8 chars; lesson 5.2 (LLM dumping whole sentences as entities).
        with pytest.raises(ValidationError):
            BannedEntity(user_id="u1", entity="一二三四五六七八九")


class TestCorrectionTarget:
    def test_construct_with_correct(self) -> None:
        t = CorrectionTarget(ref="岳西", verb="不是", correct="怀宁")
        assert t.correct == "怀宁"

    def test_construct_negation_only(self) -> None:
        t = CorrectionTarget(ref="岳西", verb="不是")
        assert t.correct is None


class TestCorrectionJudgement:
    def test_action_enum(self) -> None:
        j = CorrectionJudgement(action="deprecate", confidence=0.9, reason="user 否定")
        assert j.action == "deprecate"

    def test_confidence_bounded(self) -> None:
        with pytest.raises(ValidationError):
            CorrectionJudgement(action="deprecate", confidence=1.1)


class TestCorrectionCandidate:
    def test_construct(self) -> None:
        c = CorrectionCandidate(source="episodic", ref_id="m1", text="老家在岳西")
        assert c.source == "episodic"


class TestMemoryDeprecation:
    def test_default_action_audit_only(self) -> None:
        # Lesson 5.3: low-confidence judgements default to audit_only,
        # not deprecate, to avoid wrongful deletion.
        d = MemoryDeprecation(
            user_id="u1",
            source="episodic",
            ref_id="m1",
            llm_confidence=0.3,
        )
        assert d.action == "audit_only"
