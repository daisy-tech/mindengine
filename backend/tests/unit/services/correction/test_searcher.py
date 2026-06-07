"""Unit tests for correction.searcher (cross-layer candidate scan)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.correction import CorrectionTarget
from app.domain.memory import (
    BasicInfo,
    EpisodicMemory,
    Event,
    Profile,
    Relationship,
)
from app.services.correction.searcher import CorrectionCandidateSearcher
from tests.unit.services._fakes import (
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


def _fake_repos(user_id: str = "u1"):
    return (
        FakeProfileRepo(user_id=user_id),
        FakeEventRepo(user_id=user_id),
        FakeEpisodicRepo(user_id=user_id),
        FakeRelationshipRepo(user_id=user_id),
    )


def _searcher(p, ev, ep, rel, **kw):
    return CorrectionCandidateSearcher(
        profile_repo=p,
        event_repo=ev,
        episodic_repo=ep,
        relationship_repo=rel,
        **kw,
    )


@pytest.mark.asyncio
async def test_search_finds_episodic_substring_match():
    p, ev, ep, rel = _fake_repos()
    ep.rows.append(
        EpisodicMemory(
            id="m1", user_id="u1", text="我老家在岳西县", source="extract"
        )
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([CorrectionTarget(ref="岳西", verb="不是")])
    assert len(bundle.items) == 1
    assert bundle.items[0].source == "episodic"
    assert bundle.items[0].ref_id == "m1"


@pytest.mark.asyncio
async def test_search_finds_event_match():
    p, ev, ep, rel = _fake_repos()
    ev.rows.append(
        Event(
            id="e1", user_id="u1", type="experience", title="搬到岳西",
            content="2020 年搬到岳西", occurred_at=None, status="active",
            source_message_id=None, created_at=datetime.now(UTC),
        )
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([CorrectionTarget(ref="岳西", verb="不对")])
    sources = [c.source for c in bundle.items]
    assert "event" in sources


@pytest.mark.asyncio
async def test_search_finds_profile_field_path():
    p, ev, ep, rel = _fake_repos()
    p.profile = Profile(
        user_id="u1",
        basic=BasicInfo(name="张三", location="岳西"),
        interests=["跑步"],
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([CorrectionTarget(ref="岳西", verb="不是")])
    paths = [c.ref_id for c in bundle.items if c.source == "profile"]
    assert "basic.location" in paths


@pytest.mark.asyncio
async def test_search_finds_relationship_by_name():
    p, ev, ep, rel = _fake_repos()
    rel.rows.append(
        Relationship(
            id=str(uuid4()), user_id="u1", name="小鹏",
            role="同事", attributes={}, via=None, status="active",
            created_at=datetime.now(UTC),
        )
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([CorrectionTarget(ref="小鹏", verb="不是")])
    sources = [c.source for c in bundle.items]
    assert "entity" in sources  # relationships mapped to source="entity"


@pytest.mark.asyncio
async def test_search_dedupes_by_source_ref():
    p, ev, ep, rel = _fake_repos()
    ep.rows.append(
        EpisodicMemory(id="m1", user_id="u1", text="岳西 岳西", source="x")
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search(
        [
            CorrectionTarget(ref="岳西", verb="不是"),
            CorrectionTarget(ref="岳西", verb="不对"),
        ]
    )
    assert len([c for c in bundle.items if c.ref_id == "m1"]) == 1


@pytest.mark.asyncio
async def test_search_respects_candidate_limit():
    p, ev, ep, rel = _fake_repos()
    for i in range(20):
        ep.rows.append(
            EpisodicMemory(id=f"m{i}", user_id="u1", text="岳西", source="x")
        )
    s = _searcher(p, ev, ep, rel, candidate_limit=4)
    bundle = await s.search([CorrectionTarget(ref="岳西", verb="不是")])
    assert len(bundle.items) == 4


@pytest.mark.asyncio
async def test_search_empty_targets_returns_empty():
    p, ev, ep, rel = _fake_repos()
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([])
    assert len(bundle) == 0


@pytest.mark.asyncio
async def test_search_finds_interests_field_path():
    p, ev, ep, rel = _fake_repos()
    p.profile = Profile(
        user_id="u1",
        basic=BasicInfo(),
        interests=["弹钢琴", "下棋"],
    )
    s = _searcher(p, ev, ep, rel)
    bundle = await s.search([CorrectionTarget(ref="弹钢琴", verb="不是")])
    paths = [c.ref_id for c in bundle.items if c.source == "profile"]
    assert any(p.startswith("interests[") for p in paths)
