"""PromptComposer tests."""

from __future__ import annotations

from app.domain.memory import (
    MemoryContext,
    RoutedMemoryItem,
    SnapshotStats,
)
from app.domain.prompt import SectionKey
from app.domain.route import (
    EventPolicy,
    Intent,
    IntentSource,
    MemoryDepth,
    MemoryRoute,
    MemoryUsage,
    Personality,
)
from app.services.prompt_composer import PromptComposer
from app.services.prompt_composer.intent_guide import INTENT_GUIDES
from app.services.prompt_composer.personality import (
    PERSONALITY_CONTRACTS,
    contract_for,
)


def _route(
    *,
    intent: Intent = Intent.CASUAL,
    personality: Personality = Personality.BALANCED,
    layers: tuple[str, ...] = ("profile_basic",),
    max_explicit: int = 0,
    sensitive: bool = False,
) -> MemoryRoute:
    return MemoryRoute(
        intent=intent,
        intent_source=IntentSource.HARD_RULE,
        intent_confidence=1.0,
        personality=personality,
        memory_depth=MemoryDepth.MINIMAL,
        load_layers=list(layers),  # type: ignore[arg-type]
        sensitive_mode=sensitive,
        max_explicit_memories=max_explicit,
        event_policy=EventPolicy.NONE,
        query="hi",
    )


def _ctx_with_memory(text: str, usage: MemoryUsage) -> MemoryContext:
    ctx = MemoryContext()
    ctx.relevant_memories.append(
        RoutedMemoryItem(source="episodic", ref_id="m1", text=text, usage=usage.value, score=0.9)
    )
    ctx.snapshot_stats = SnapshotStats(episodic_total=1)
    return ctx


# ─── personality contracts ──────────────────────────────────────


def test_contracts_have_three_personalities() -> None:
    assert set(PERSONALITY_CONTRACTS.keys()) == {
        Personality.INTROVERT,
        Personality.BALANCED,
        Personality.EXTROVERT,
    }


def test_contract_caps_increase_with_extraversion() -> None:
    intro = contract_for(Personality.INTROVERT)
    bal = contract_for(Personality.BALANCED)
    extra = contract_for(Personality.EXTROVERT)
    assert intro.max_chars < bal.max_chars < extra.max_chars
    assert intro.allow_question is False
    assert bal.allow_question is True


def test_intent_guide_for_each_intent() -> None:
    for intent in Intent:
        assert intent in INTENT_GUIDES, f"missing guide for {intent}"


# ─── composer basic shape ─────────────────────────────────────


def test_compose_returns_pack_with_excerpt_under_500_chars() -> None:
    composer = PromptComposer()
    pack = composer.compose(route=_route(), ctx=MemoryContext())
    assert pack.system
    assert pack.meta.system_excerpt
    assert len(pack.meta.system_excerpt) <= composer.excerpt_max_chars + 1  # +1 for "…"


def test_compose_section_keys_match_keys_in_meta() -> None:
    composer = PromptComposer()
    pack = composer.compose(route=_route(), ctx=MemoryContext())
    populated = [k for k, v in pack.sections.items() if v]
    assert set(pack.meta.section_keys) == set(populated)
    # Base persona + hard rules + personality contract are always present.
    assert SectionKey.BASE_PERSONA in populated
    assert SectionKey.HARD_RULES in populated
    assert SectionKey.PERSONALITY_CONTRACT in populated


def test_compose_omits_empty_sections() -> None:
    composer = PromptComposer()
    pack = composer.compose(route=_route(), ctx=MemoryContext())
    assert pack.sections.get(SectionKey.PROFILE) is None
    assert pack.sections.get(SectionKey.EXPLICIT_MEMORIES) is None


def test_compose_includes_personality_contract_text() -> None:
    composer = PromptComposer()
    pack = composer.compose(
        route=_route(personality=Personality.INTROVERT),
        ctx=MemoryContext(),
    )
    assert "内向" in pack.system


def test_compose_includes_intent_guide() -> None:
    composer = PromptComposer()
    pack = composer.compose(
        route=_route(intent=Intent.MEMORY_CHALLENGE),
        ctx=MemoryContext(),
    )
    assert "记忆质询" in pack.system or "记得" in pack.system


def test_sensitive_mode_uses_sensitive_turn_rules() -> None:
    composer = PromptComposer()
    pack = composer.compose(
        route=_route(intent=Intent.EMOTIONAL_SUPPORT, sensitive=True),
        ctx=MemoryContext(),
    )
    assert "敏感模式" in pack.system


# ─── memory selection ────────────────────────────────────────


def test_explicit_memory_section_appears_when_quota_available() -> None:
    composer = PromptComposer()
    ctx = _ctx_with_memory("用户老家在怀宁", MemoryUsage.EXPLICIT_OK)
    pack = composer.compose(
        route=_route(intent=Intent.MEMORY_CHALLENGE, max_explicit=2),
        ctx=ctx,
    )
    assert pack.sections[SectionKey.EXPLICIT_MEMORIES] is not None
    assert "怀宁" in pack.system
    assert pack.activated_items[0].ref_id == "m1"


def test_background_only_items_not_in_explicit_pool() -> None:
    composer = PromptComposer()
    ctx = _ctx_with_memory("用户去年比较辛苦", MemoryUsage.BACKGROUND_ONLY)
    pack = composer.compose(
        route=_route(
            intent=Intent.EMOTIONAL_SUPPORT,
            sensitive=True,
            max_explicit=2,
        ),
        ctx=ctx,
    )
    # Memory was background-tagged; should not be picked into the explicit pool
    # even though max_explicit=2.
    assert pack.activated_items == []
    assert pack.sections[SectionKey.EXPLICIT_MEMORIES] is None


def test_max_explicit_limits_count() -> None:
    composer = PromptComposer()
    ctx = MemoryContext()
    for i in range(5):
        ctx.relevant_memories.append(
            RoutedMemoryItem(
                source="episodic",
                ref_id=f"m{i}",
                text=f"记忆{i}",
                usage=MemoryUsage.EXPLICIT_OK.value,
            )
        )
    pack = composer.compose(
        route=_route(intent=Intent.MEMORY_CHALLENGE, max_explicit=2),
        ctx=ctx,
    )
    assert len(pack.activated_items) == 2


def test_meta_round_trips_through_json() -> None:
    composer = PromptComposer()
    ctx = _ctx_with_memory("hi", MemoryUsage.EXPLICIT_OK)
    pack = composer.compose(
        route=_route(intent=Intent.MEMORY_CHALLENGE, max_explicit=1),
        ctx=ctx,
    )
    raw = pack.meta.model_dump_json()
    # Just ensure it parses + the activated list survives.
    from app.domain.prompt import PromptMeta

    again = PromptMeta.model_validate_json(raw)
    assert again.activated[0].ref_id == "m1"
