"""Tests for domain.route."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.route import (
    ClassifyResult,
    EventPolicy,
    Intent,
    IntentSource,
    MemoryDepth,
    MemoryRoute,
    MemoryUsage,
    Personality,
    RoutedMemory,
)


class TestIntent:
    def test_nine_intents(self) -> None:
        # The 9 stable intents per doc 03 §2 — guard against accidental drift.
        assert {i.value for i in Intent} == {
            "casual",
            "self_summary",
            "memory_challenge",
            "relationship_topic",
            "emotional_support",
            "plan_followup",
            "preference_request",
            "correction",
            "knowledge_task",
        }

    def test_intent_is_string(self) -> None:
        assert Intent.CORRECTION == "correction"
        assert isinstance(Intent.CORRECTION, str)


class TestMemoryUsage:
    def test_four_usage_tags(self) -> None:
        assert {u.value for u in MemoryUsage} == {
            "EXPLICIT_OK",
            "BACKGROUND_ONLY",
            "FOLLOW_UP_ONCE",
            "AVOID_UNLESS_ASKED",
        }


class TestPersonality:
    def test_three_personalities(self) -> None:
        assert {p.value for p in Personality} == {"introvert", "balanced", "extrovert"}


class TestClassifyResult:
    def test_confidence_bounded(self) -> None:
        ClassifyResult(intent=Intent.CASUAL, confidence=0.0)
        ClassifyResult(intent=Intent.CASUAL, confidence=1.0)
        with pytest.raises(ValidationError):
            ClassifyResult(intent=Intent.CASUAL, confidence=-0.1)
        with pytest.raises(ValidationError):
            ClassifyResult(intent=Intent.CASUAL, confidence=1.1)


class TestMemoryRoute:
    def _route(self, **overrides) -> MemoryRoute:
        defaults = {
            "intent": Intent.CASUAL,
            "intent_source": IntentSource.HARD_RULE,
            "intent_confidence": 1.0,
            "personality": Personality.BALANCED,
            "memory_depth": MemoryDepth.MINIMAL,
            "load_layers": ["profile_basic"],
        }
        defaults.update(overrides)
        return MemoryRoute(**defaults)

    def test_minimal_construction(self) -> None:
        r = self._route()
        assert r.schema_version == 1
        assert r.intent == Intent.CASUAL
        assert r.event_policy == EventPolicy.NONE
        assert r.max_explicit_memories == 0
        assert r.sensitive_mode is False

    def test_max_explicit_bounded(self) -> None:
        with pytest.raises(ValidationError):
            self._route(max_explicit_memories=6)
        with pytest.raises(ValidationError):
            self._route(max_explicit_memories=-1)

    def test_load_layers_subset(self) -> None:
        # Literal type rejects unknown layer names.
        with pytest.raises(ValidationError):
            self._route(load_layers=["bogus_layer"])

    def test_serialization_roundtrip(self) -> None:
        r = self._route(
            load_layers=["profile_basic", "episodic"],
            reasons=["hard_rule:correction"],
        )
        raw = r.model_dump_json()
        r2 = MemoryRoute.model_validate_json(raw)
        assert r2 == r


class TestRoutedMemory:
    def test_construct(self) -> None:
        m = RoutedMemory(
            source="profile",
            ref_id="profile:basic.name",
            text="姓名: 张三",
            usage=MemoryUsage.EXPLICIT_OK,
        )
        assert m.usage == MemoryUsage.EXPLICIT_OK
        assert m.score is None
