"""Tests for domain.prompt."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.prompt import (
    ContractEnforcement,
    LLMRequestRef,
    MemoryRef,
    PromptMeta,
    PromptPack,
    SectionKey,
)
from app.domain.route import (
    Intent,
    IntentSource,
    MemoryDepth,
    MemoryRoute,
    Personality,
)


def _make_meta() -> PromptMeta:
    return PromptMeta(
        composed_at=datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
        model="qwen3.7-max",
        route=MemoryRoute(
            intent=Intent.CASUAL,
            intent_source=IntentSource.HARD_RULE,
            intent_confidence=1.0,
            personality=Personality.BALANCED,
            memory_depth=MemoryDepth.MINIMAL,
        ),
        section_keys=[SectionKey.BASE_PERSONA, SectionKey.HARD_RULES],
        system_excerpt="你是小白...",
        llm_request=LLMRequestRef(
            model="qwen3.7-max", temperature=0.7, enable_thinking=False
        ),
    )


def test_prompt_meta_round_trip() -> None:
    m = _make_meta()
    raw = m.model_dump_json()
    m2 = PromptMeta.model_validate_json(raw)
    assert m2 == m


def test_prompt_meta_excerpt_kept_short() -> None:
    # Lesson 3.4: full system prompt MUST NOT be persisted in PromptMeta.
    # We don't enforce a length here at the type level (caller's job),
    # but we do verify the field is named `system_excerpt` not `system`.
    fields = PromptMeta.model_fields
    assert "system_excerpt" in fields
    assert "system" not in fields


def test_section_keys_set() -> None:
    keys = {k.value for k in SectionKey}
    assert "base_persona" in keys
    assert "personality_contract" in keys
    assert "hard_rules" in keys


def test_contract_enforcement_defaults() -> None:
    ce = ContractEnforcement()
    assert ce.truncated is False
    assert ce.removed_question is False
    assert ce.raw_char_count is None


def test_prompt_pack_sections_keyed_by_enum() -> None:
    pack = PromptPack(
        system="你是小白...\n...",
        sections={
            SectionKey.BASE_PERSONA: "你是小白...",
            SectionKey.HARD_RULES: "≪硬边界≫",
            SectionKey.PROFILE: None,  # empty section is None, not "（无）"
        },
        meta=_make_meta(),
    )
    # Lesson 3.5: structured access, not string match.
    assert pack.sections[SectionKey.PROFILE] is None
    assert pack.sections[SectionKey.BASE_PERSONA].startswith("你是小白")


def test_memory_ref_compact() -> None:
    ref = MemoryRef(source="episodic", ref_id="m1", excerpt="泡茶")
    assert ref.source == "episodic"
