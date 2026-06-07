"""Tests for per-intent dispatch-target table."""

from __future__ import annotations

from app.domain.route import Intent
from app.services.memory_extract import targets_for
from app.services.memory_extract.dispatch_targets import (
    EXTRACT_TARGETS_BY_INTENT,
)


def test_every_intent_is_covered():
    for intent in Intent:
        assert intent in EXTRACT_TARGETS_BY_INTENT, intent


def test_correction_disables_extraction():
    """Doc 06 §2: extract and correction must be mutually exclusive."""
    assert targets_for(Intent.CORRECTION) == frozenset()


def test_knowledge_task_extracts_nothing():
    assert targets_for(Intent.KNOWLEDGE_TASK) == frozenset()


def test_self_summary_and_memory_challenge_extract_nothing():
    assert targets_for(Intent.SELF_SUMMARY) == frozenset()
    assert targets_for(Intent.MEMORY_CHALLENGE) == frozenset()


def test_casual_extracts_all_layers():
    assert targets_for(Intent.CASUAL) == frozenset(
        {"episodic", "profile", "event", "relationship"}
    )


def test_relationship_topic_extracts_relationship_and_episodic():
    targets = targets_for(Intent.RELATIONSHIP_TOPIC)
    assert "relationship" in targets
    assert "episodic" in targets


def test_preference_request_extracts_profile():
    assert "profile" in targets_for(Intent.PREFERENCE_REQUEST)


def test_emotional_support_extracts_event_and_episodic():
    targets = targets_for(Intent.EMOTIONAL_SUPPORT)
    assert "episodic" in targets
    assert "event" in targets
