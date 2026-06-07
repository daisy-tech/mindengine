"""MemoryContextLoader tests using fakes."""

from __future__ import annotations

import pytest

from app.domain.correction import BannedEntity, MemoryDeprecation
from app.domain.memory import (
    BasicInfo,
    EpisodicHit,
    Event,
    Profile,
    Relationship,
)
from app.domain.route import (
    EventPolicy,
    Intent,
    IntentSource,
    MemoryDepth,
    MemoryRoute,
    MemoryUsage,
    Personality,
)
from app.services.memory_context import MemoryContextLoader
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeDeprecationRepo,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


def _route(
    *,
    intent: Intent,
    layers: tuple[str, ...],
    max_explicit: int = 0,
    sensitive: bool = False,
    event_policy: EventPolicy = EventPolicy.NONE,
) -> MemoryRoute:
    return MemoryRoute(
        intent=intent,
        intent_source=IntentSource.HARD_RULE,
        intent_confidence=1.0,
        personality=Personality.BALANCED,
        memory_depth=MemoryDepth.FOCUSED,
        load_layers=list(layers),  # type: ignore[arg-type]
        sensitive_mode=sensitive,
        max_explicit_memories=max_explicit,
        event_policy=event_policy,
        query="老家在哪",
    )


def _make_loader(uid: str = "u1") -> MemoryContextLoader:
    return MemoryContextLoader(
        profile_repo=FakeProfileRepo(user_id=uid),
        event_repo=FakeEventRepo(user_id=uid),
        episodic_repo=FakeEpisodicRepo(user_id=uid),
        relationship_repo=FakeRelationshipRepo(user_id=uid),
        banned_repo=FakeBannedEntityRepo(user_id=uid),
        deprecation_repo=FakeDeprecationRepo(user_id=uid),
    )


@pytest.mark.asyncio
async def test_minimal_route_loads_nothing() -> None:
    loader = _make_loader()
    ctx = await loader.load(_route(intent=Intent.CASUAL, layers=()))
    assert ctx.stable_profile == []
    assert ctx.relevant_memories == []
    assert ctx.snapshot_stats.episodic_total == 0


@pytest.mark.asyncio
async def test_profile_basic_returns_compact_items() -> None:
    loader = _make_loader()
    loader.profile_repo.profile = Profile(  # type: ignore[attr-defined]
        user_id="u1",
        basic=BasicInfo(name="张三", location="怀宁", birth_year=1990),
        interests=["茶", "登山"],
    )
    ctx = await loader.load(_route(intent=Intent.CASUAL, layers=("profile_basic",)))
    texts = [it.text for it in ctx.stable_profile]
    assert any("张三" in t for t in texts)
    assert any("怀宁" in t for t in texts)
    # interests not surfaced under profile_basic
    assert not any("茶" in t for t in texts)


@pytest.mark.asyncio
async def test_profile_full_includes_interests_and_family() -> None:
    loader = _make_loader()
    loader.profile_repo.profile = Profile(  # type: ignore[attr-defined]
        user_id="u1",
        basic=BasicInfo(name="李四"),
        interests=["茶"],
    )
    ctx = await loader.load(_route(intent=Intent.SELF_SUMMARY, layers=("profile",)))
    texts = " ".join(it.text for it in ctx.stable_profile)
    assert "茶" in texts


@pytest.mark.asyncio
async def test_relationships_only_for_friendly_intents() -> None:
    loader = _make_loader()
    loader.relationship_repo.rows = [  # type: ignore[attr-defined]
        Relationship(id="r1", user_id="u1", name="张三", role="妻子"),
    ]
    casual = await loader.load(_route(intent=Intent.CASUAL, layers=("relationships",)))
    assert casual.relevant_relationships == []
    rel = await loader.load(
        _route(intent=Intent.RELATIONSHIP_TOPIC, layers=("relationships",))
    )
    assert len(rel.relevant_relationships) == 1
    assert "妻子" in rel.relevant_relationships[0].text


@pytest.mark.asyncio
async def test_events_summary_uses_explicit_ok() -> None:
    loader = _make_loader()
    loader.event_repo.rows = [  # type: ignore[attr-defined]
        Event(id="e1", user_id="u1", type="plan", title="周末爬山", content="周日"),
    ]
    ctx = await loader.load(
        _route(intent=Intent.PLAN_FOLLOWUP, layers=("events",), event_policy=EventPolicy.SUMMARY)
    )
    assert len(ctx.relevant_events) == 1
    assert ctx.relevant_events[0].usage == MemoryUsage.EXPLICIT_OK.value


@pytest.mark.asyncio
async def test_events_pain_points_go_to_background_only() -> None:
    loader = _make_loader()
    loader.event_repo.rows = [  # type: ignore[attr-defined]
        Event(id="e1", user_id="u1", type="challenge", title="工作压力", content="..."),
    ]
    ctx = await loader.load(
        _route(
            intent=Intent.EMOTIONAL_SUPPORT,
            layers=("events",),
            sensitive=True,
            event_policy=EventPolicy.BACKGROUND_PAIN_POINTS,
        )
    )
    assert ctx.relevant_events == []
    assert len(ctx.background_only) == 1
    assert ctx.background_only[0].usage == MemoryUsage.BACKGROUND_ONLY.value


@pytest.mark.asyncio
async def test_episodic_hits_filtered_by_banned_entities() -> None:
    loader = _make_loader()
    loader.episodic_repo.search_results = [  # type: ignore[attr-defined]
        EpisodicHit(id="m1", text="老家在岳西", score=0.9),
        EpisodicHit(id="m2", text="老家在怀宁", score=0.85),
    ]
    loader.banned_repo.rows = [BannedEntity(user_id="u1", entity="岳西")]  # type: ignore[attr-defined]
    ctx = await loader.load(
        _route(intent=Intent.MEMORY_CHALLENGE, layers=("episodic",), max_explicit=3)
    )
    assert [m.ref_id for m in ctx.relevant_memories] == ["m2"]
    assert ctx.snapshot_stats.banned_entities == ["岳西"]


@pytest.mark.asyncio
async def test_episodic_hits_filtered_by_deprecations() -> None:
    loader = _make_loader()
    loader.episodic_repo.search_results = [  # type: ignore[attr-defined]
        EpisodicHit(id="m1", text="泡茶很有讲究", score=0.9),
        EpisodicHit(id="m2", text="喜欢登山", score=0.85),
    ]
    await loader.deprecation_repo.insert(
        MemoryDeprecation(
            user_id="u1", source="episodic", ref_id="m1", action="deprecate"
        )
    )
    ctx = await loader.load(
        _route(intent=Intent.MEMORY_CHALLENGE, layers=("episodic",), max_explicit=3)
    )
    kept = [m.ref_id for m in ctx.relevant_memories]
    assert "m1" not in kept
    assert "m2" in kept


@pytest.mark.asyncio
async def test_sensitive_episodic_lands_in_relevant_pool_with_background_usage() -> None:
    # Doc 04 §6: even when episodic is loaded for sensitive intents, the
    # composer must keep them as background; loader expresses that via the
    # `usage` tag on each item.
    loader = _make_loader()
    loader.episodic_repo.search_results = [  # type: ignore[attr-defined]
        EpisodicHit(id="m1", text="去年家人吵过", score=0.9),
    ]
    ctx = await loader.load(
        _route(
            intent=Intent.EMOTIONAL_SUPPORT,
            layers=("episodic",),
            max_explicit=1,
            sensitive=True,
        )
    )
    assert len(ctx.relevant_memories) == 1
    assert ctx.relevant_memories[0].usage == MemoryUsage.BACKGROUND_ONLY.value


@pytest.mark.asyncio
async def test_loader_reports_snapshot_stats() -> None:
    loader = _make_loader()
    loader.profile_repo.profile = Profile(  # type: ignore[attr-defined]
        user_id="u1", basic=BasicInfo(name="张三")
    )
    loader.event_repo.rows = [  # type: ignore[attr-defined]
        Event(id="e1", user_id="u1", type="plan", title="x", content="y"),
    ]
    ctx = await loader.load(
        _route(
            intent=Intent.SELF_SUMMARY,
            layers=("profile", "events"),
            event_policy=EventPolicy.SUMMARY,
            max_explicit=2,
        )
    )
    assert ctx.snapshot_stats.profile_total > 0
    assert ctx.snapshot_stats.event_total == 1
