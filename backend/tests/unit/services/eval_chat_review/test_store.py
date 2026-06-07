"""Tests for the review store: path safety, save/load/list/delete."""

from __future__ import annotations

import json

import pytest

from app.services.eval_chat_review.store import (
    delete_review,
    list_stored_summaries,
    load_review,
    safe_id_segment,
    save_review,
)


def _review(rate: float = 0.5) -> dict:
    return {
        "schema": "eval_review_v1",
        "evaluated_at": "2026-06-07T12:00:00+00:00",
        "review": {
            "evaluable_turns": 4,
            "final_ok_rate": rate,
            "structure_pass_rate": 1.0,
            "counters": {
                "final_good": 1,
                "final_ok": 1,
                "final_suspicious": 1,
                "final_bad": 1,
            },
            "rule_stats": {},
            "root_cause_top": [],
        },
        "turns": [],
    }


def test_safe_id_segment_accepts_simple_ids():
    assert safe_id_segment("user-1_v2") == "user-1_v2"


def test_safe_id_segment_rejects_traversal():
    with pytest.raises(ValueError):
        safe_id_segment("../etc")
    with pytest.raises(ValueError):
        safe_id_segment("a/b")
    with pytest.raises(ValueError):
        safe_id_segment("")


def test_save_then_load_round_trip(tmp_path):
    saved = save_review(
        root=tmp_path,
        user_id="alice",
        conversation_id="conv1",
        review=_review(0.75),
    )
    assert saved.exists()
    parsed = json.loads(saved.read_text())
    assert parsed["review"]["final_ok_rate"] == 0.75

    loaded = load_review(root=tmp_path, user_id="alice", conversation_id="conv1")
    assert loaded is not None
    assert loaded["review"]["final_ok_rate"] == 0.75


def test_load_returns_none_when_missing(tmp_path):
    assert (
        load_review(root=tmp_path, user_id="bob", conversation_id="missing") is None
    )


def test_save_rejects_unsafe_user_id(tmp_path):
    with pytest.raises(ValueError):
        save_review(
            root=tmp_path,
            user_id="../escape",
            conversation_id="conv",
            review=_review(),
        )


def test_save_rejects_unsafe_conv_id(tmp_path):
    with pytest.raises(ValueError):
        save_review(
            root=tmp_path,
            user_id="alice",
            conversation_id="../etc",
            review=_review(),
        )


def test_list_stored_summaries_orders_by_evaluated_at_desc(tmp_path):
    save_review(
        root=tmp_path,
        user_id="alice",
        conversation_id="conv-old",
        review={**_review(), "evaluated_at": "2026-06-01T00:00:00+00:00"},
    )
    save_review(
        root=tmp_path,
        user_id="alice",
        conversation_id="conv-new",
        review={**_review(), "evaluated_at": "2026-06-07T00:00:00+00:00"},
    )
    out = list_stored_summaries(root=tmp_path, user_id="alice")
    assert [it.conversation_id for it in out] == ["conv-new", "conv-old"]


def test_delete_review_removes_file(tmp_path):
    save_review(
        root=tmp_path,
        user_id="alice",
        conversation_id="conv",
        review=_review(),
    )
    assert (
        delete_review(root=tmp_path, user_id="alice", conversation_id="conv") is True
    )
    assert (
        delete_review(root=tmp_path, user_id="alice", conversation_id="conv") is False
    )


def test_list_stored_summaries_skips_corrupt_files(tmp_path):
    base = tmp_path / "alice"
    base.mkdir(parents=True)
    (base / "good.json").write_text(json.dumps(_review()))
    (base / "broken.json").write_text("{not-json")
    out = list_stored_summaries(root=tmp_path, user_id="alice")
    assert [it.conversation_id for it in out] == ["good"]
