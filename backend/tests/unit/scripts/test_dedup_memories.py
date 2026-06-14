"""Pure-function tests for the historical dedup CLI's planner.

The DB-bound paths (``_dedup_episodic_for_user``, ``_dedup_event_for_user``)
need a real Postgres + pgvector and are exercised by the selftest harness.
Here we lock in the planner: given a chronological list of events, which
ones do we tag as duplicates of which?
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.dedup_memories import EventRecord, plan_event_dedup


def _ts(minute: int) -> datetime:
    base = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    return base + timedelta(minutes=minute)


def test_plan_event_dedup_empty():
    assert plan_event_dedup([]) == []


def test_plan_event_dedup_single():
    rows = [EventRecord(id="e1", type="experience", title="宅家学习AI", created_at=_ts(0))]
    assert plan_event_dedup(rows) == []


def test_plan_event_dedup_exact_duplicate_collapses_onto_earliest():
    rows = [
        EventRecord(id="e1", type="experience", title="宅家学习AI", created_at=_ts(0)),
        EventRecord(id="e2", type="experience", title="宅家学习AI", created_at=_ts(5)),
    ]
    plan = plan_event_dedup(rows)
    assert len(plan) == 1
    assert plan[0].dup_id == "e2"
    assert plan[0].keep_id == "e1"
    assert plan[0].reason == "exact"


def test_plan_event_dedup_normalizes_punctuation():
    """`"宅家学习AI"` and `"宅家学习AI."` collapse via the title normalizer."""
    rows = [
        EventRecord(id="e1", type="experience", title="宅家学习AI", created_at=_ts(0)),
        EventRecord(id="e2", type="experience", title="宅家学习AI.", created_at=_ts(3)),
        EventRecord(id="e3", type="experience", title="宅家学习AI。", created_at=_ts(6)),
    ]
    plan = plan_event_dedup(rows)
    dup_ids = {p.dup_id for p in plan}
    keep_ids = {p.keep_id for p in plan}
    assert dup_ids == {"e2", "e3"}
    assert keep_ids == {"e1"}
    assert all(p.reason == "exact" for p in plan)


def test_plan_event_dedup_substring_match():
    """Long candidate that contains the keeper's title gets folded in."""
    rows = [
        EventRecord(id="e1", type="experience", title="宅家学习AI", created_at=_ts(0)),
        EventRecord(
            id="e2",
            type="experience",
            title="宅家学习AI 顺便陪猫",
            created_at=_ts(10),
        ),
    ]
    plan = plan_event_dedup(rows)
    assert plan == [
        type(plan[0])(dup_id="e2", keep_id="e1", reason="substring"),
    ]


def test_plan_event_dedup_substring_too_short_does_not_match():
    """≤ 3 char overlap should NOT collapse — too noisy a signal."""
    rows = [
        EventRecord(id="e1", type="experience", title="AI", created_at=_ts(0)),
        EventRecord(id="e2", type="experience", title="AI 周末复习", created_at=_ts(5)),
    ]
    plan = plan_event_dedup(rows)
    assert plan == []  # "AI" 长度 2,低于 4 字门槛


def test_plan_event_dedup_different_types_dont_collide():
    """Same title in two types is two distinct events."""
    rows = [
        EventRecord(id="e1", type="experience", title="面试 X 公司", created_at=_ts(0)),
        EventRecord(id="e2", type="plan", title="面试 X 公司", created_at=_ts(5)),
    ]
    assert plan_event_dedup(rows) == []


def test_plan_event_dedup_blank_title_is_ignored():
    """Empty / whitespace-only titles never become keepers and never dedup."""
    rows = [
        EventRecord(id="e1", type="experience", title="", created_at=_ts(0)),
        EventRecord(id="e2", type="experience", title="   ", created_at=_ts(5)),
        EventRecord(id="e3", type="experience", title="宅家学习AI", created_at=_ts(10)),
        EventRecord(id="e4", type="experience", title="宅家学习AI", created_at=_ts(15)),
    ]
    plan = plan_event_dedup(rows)
    # 只有 e3/e4 进入比较;e4 是 e3 的重复
    assert plan == [type(plan[0])(dup_id="e4", keep_id="e3", reason="exact")]


def test_plan_event_dedup_preserves_chronology():
    """When 3 duplicates exist, all collapse onto the earliest, not pairwise."""
    rows = [
        EventRecord(id="e1", type="challenge", title="最近心情烦躁", created_at=_ts(0)),
        EventRecord(id="e2", type="challenge", title="最近心情烦躁", created_at=_ts(5)),
        EventRecord(id="e3", type="challenge", title="最近心情烦躁", created_at=_ts(10)),
    ]
    plan = plan_event_dedup(rows)
    assert {p.dup_id for p in plan} == {"e2", "e3"}
    # 关键:两个重复都指向 e1,不是 e3 → e2 → e1 链式
    assert all(p.keep_id == "e1" for p in plan)
