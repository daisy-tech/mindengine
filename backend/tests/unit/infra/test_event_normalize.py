"""Pure-function tests for event title normalization (no DB).

The `_normalize_title` helper underpins event-layer dedup. We avoid
spinning up a real Postgres for the substring-match path; we just want
to lock in the wire-format expectations.
"""

from __future__ import annotations

from app.infra.repositories.event_repo import _normalize_title


def test_strip_trailing_chinese_period():
    assert _normalize_title("宅家学习AI。") == "宅家学习ai"


def test_strip_trailing_ascii_period():
    assert _normalize_title("宅家学习AI.") == "宅家学习ai"


def test_strip_multiple_trailing_punct():
    assert _normalize_title("最近心情烦躁!!!") == "最近心情烦躁"
    assert _normalize_title("最近心情烦躁……") == "最近心情烦躁"


def test_lowercases_ascii():
    assert _normalize_title("Foo Bar") == "foo bar"


def test_blank():
    assert _normalize_title("") == ""
    assert _normalize_title("   ") == ""
    assert _normalize_title("\u3000") == ""


def test_idempotent():
    once = _normalize_title("宅家学习AI。")
    twice = _normalize_title(once)
    assert once == twice
