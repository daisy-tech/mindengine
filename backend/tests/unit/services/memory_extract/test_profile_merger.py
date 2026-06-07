"""Tests for ProfileMerger (pure logic — no IO)."""

from __future__ import annotations

from app.domain.memory import Profile
from app.services.memory_extract import ProfileMerger, merge_profile


def test_merge_creates_profile_when_current_is_none():
    partial = {
        "user_id": "u1",
        "basic": {"name": "张三"},
        "interests": ["羽毛球"],
    }
    merged = merge_profile(None, partial)
    assert merged.user_id == "u1"
    assert merged.basic.name == "张三"
    assert merged.interests == ["羽毛球"]


def test_merge_requires_user_id_when_current_is_none():
    import pytest

    with pytest.raises(ValueError):
        merge_profile(None, {"basic": {"name": "X"}})


def test_override_field_replaces_existing_value():
    cur = Profile(user_id="u1")
    cur.basic.name = "旧名字"
    merged = merge_profile(cur, {"basic": {"name": "新名字"}})
    assert merged.basic.name == "新名字"


def test_override_field_ignores_none_values():
    cur = Profile(user_id="u1")
    cur.basic.name = "张三"
    merged = merge_profile(cur, {"basic": {"name": None}})
    assert merged.basic.name == "张三"


def test_override_drops_unknown_fields_silently():
    """Lesson 4.1: unknown fields must not pollute Profile."""
    cur = Profile(user_id="u1")
    merged = merge_profile(cur, {"basic": {"random_field": "boom"}})
    assert not hasattr(merged.basic, "random_field")


def test_interests_are_unioned_and_deduped_case_insensitive():
    cur = Profile(user_id="u1", interests=["羽毛球", "Reading"])
    merged = merge_profile(
        cur, {"interests": ["羽毛球", "reading", "围棋", " 围棋 "]}
    )
    # Order preserved: existing items first, new items appended once.
    assert merged.interests == ["羽毛球", "Reading", "围棋"]


def test_interests_capped_at_default():
    seed = [f"hobby_{i}" for i in range(35)]
    merged = merge_profile(Profile(user_id="u1"), {"interests": seed})
    assert len(merged.interests) == 30


def test_interests_caps_can_be_tightened_via_dataclass():
    merger = ProfileMerger(interests_cap=3)
    merged = merger.merge(
        Profile(user_id="u1"),
        {"interests": ["a", "b", "c", "d", "e"]},
    )
    assert merged.interests == ["a", "b", "c"]


def test_extra_field_accepts_strings_and_coerces_primitives():
    merged = merge_profile(
        Profile(user_id="u1"),
        {"extra": {"city": "Shanghai", "age_hint": 30, "married": True, "skip": None}},
    )
    assert merged.extra["city"] == "Shanghai"
    assert merged.extra["age_hint"] == "30"
    assert merged.extra["married"] == "True"
    assert "skip" not in merged.extra


def test_extra_drops_oversized_keys_silently():
    long_key = "k" * 100
    merged = merge_profile(
        Profile(user_id="u1"), {"extra": {long_key: "val", "ok": "1"}}
    )
    assert long_key not in merged.extra
    assert merged.extra["ok"] == "1"


def test_correction_source_records_user_corrections():
    cur = Profile(user_id="u1")
    cur.basic.name = "李四"
    merged = merge_profile(
        cur,
        {"basic": {"name": "张三"}, "__source__": "correction"},
    )
    assert merged.basic.name == "张三"
    assert len(merged.user_corrections) == 1
    correction = merged.user_corrections[0]
    assert correction.field_path == "basic.name"
    assert correction.old_value == "李四"
    assert correction.new_value == "张三"


def test_no_correction_recorded_when_value_unchanged():
    cur = Profile(user_id="u1")
    cur.basic.name = "张三"
    merged = merge_profile(
        cur,
        {"basic": {"name": "张三"}, "__source__": "correction"},
    )
    assert merged.user_corrections == []


def test_extraction_source_does_not_record_correction():
    cur = Profile(user_id="u1")
    cur.basic.name = "李四"
    merged = merge_profile(cur, {"basic": {"name": "张三"}})
    assert merged.user_corrections == []


def test_family_structure_notes_dedup_and_keep_existing_structure_when_blank():
    cur = Profile(user_id="u1")
    cur.family_structure.structure = "三口之家"
    cur.family_structure.notes = ["儿子小宝5岁"]
    merged = merge_profile(
        cur,
        {
            "family_structure": {
                "structure": "",  # blank → keep existing
                "notes": ["儿子小宝5岁", "妻子李娜"],
            }
        },
    )
    assert merged.family_structure.structure == "三口之家"
    assert merged.family_structure.notes == ["儿子小宝5岁", "妻子李娜"]


def test_partial_with_empty_dicts_is_a_noop():
    cur = Profile(user_id="u1", interests=["羽毛球"])
    merged = merge_profile(cur, {})
    assert merged.interests == ["羽毛球"]
    assert merged.basic == cur.basic
    assert merged.updated_at is not None  # always touched


def test_updated_at_is_refreshed_each_merge():
    cur = Profile(user_id="u1")
    merged = merge_profile(cur, {"basic": {"name": "张三"}})
    assert merged.updated_at is not None
