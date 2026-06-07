"""Tests for domain.memory."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domain.memory import (
    BasicInfo,
    EpisodicHit,
    EpisodicMemory,
    Event,
    MemoryContext,
    Profile,
    Relationship,
    RoutedMemoryItem,
    SnapshotStats,
    UserCorrection,
)


class TestProfile:
    def test_defaults(self) -> None:
        p = Profile(user_id="u1")
        assert p.schema_version == 1
        assert p.basic == BasicInfo()
        assert p.interests == []
        assert p.user_corrections == []

    def test_user_correction(self, fixed_now: datetime) -> None:
        p = Profile(
            user_id="u1",
            user_corrections=[
                UserCorrection(
                    field_path="basic.location",
                    old_value="怀化",
                    new_value="怀宁",
                    happened_at=fixed_now,
                )
            ],
        )
        assert p.user_corrections[0].new_value == "怀宁"

    def test_interest_default_factory_independence(self) -> None:
        # Lesson 10.2: never use default=list; default_factory keeps instances independent.
        a = Profile(user_id="a")
        b = Profile(user_id="b")
        a.interests.append("茶")
        assert b.interests == []


class TestEvent:
    def test_default_status_active(self) -> None:
        e = Event(id="e1", user_id="u1", type="experience", title="周末", content="...")
        assert e.status == "active"

    def test_title_max_length(self) -> None:
        with pytest.raises(ValidationError):
            Event(
                id="e1",
                user_id="u1",
                type="experience",
                title="x" * 201,
                content="...",
            )


class TestEpisodic:
    def test_episodic_status_default(self) -> None:
        m = EpisodicMemory(id="m1", user_id="u1", text="...")
        assert m.status == "active"

    def test_hit_score_bounded(self) -> None:
        EpisodicHit(id="m1", text="...", score=0.5)
        with pytest.raises(ValidationError):
            EpisodicHit(id="m1", text="...", score=1.5)


class TestRelationship:
    def test_no_self_loop(self) -> None:
        # Lesson 4.3: self-loop crashes social graph UI.
        with pytest.raises(ValidationError):
            Relationship(
                id="r1",
                user_id="u1",
                name="妻子",
                role="spouse",
                via="r1",
            )

    def test_via_other_ok(self) -> None:
        rel = Relationship(
            id="r1",
            user_id="u1",
            name="女儿",
            role="child",
            via="r0",
        )
        assert rel.via == "r0"


class TestMemoryContext:
    def test_defaults(self) -> None:
        ctx = MemoryContext()
        assert ctx.snapshot_stats == SnapshotStats()
        assert ctx.relevant_memories == []

    def test_populated(self) -> None:
        item = RoutedMemoryItem(
            source="profile",
            ref_id="profile:basic.name",
            text="姓名: 张三",
            usage="EXPLICIT_OK",
        )
        ctx = MemoryContext(stable_profile=[item])
        assert ctx.stable_profile[0].source == "profile"

    def test_snapshot_stats_default_factory(self) -> None:
        a = MemoryContext()
        b = MemoryContext()
        a.snapshot_stats.banned_entities.append("岳西")
        assert b.snapshot_stats.banned_entities == []
