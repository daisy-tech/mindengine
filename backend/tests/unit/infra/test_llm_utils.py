"""Tests for infra.llm.utils.strip_json_fences."""

from __future__ import annotations

from app.infra.llm.utils import strip_json_fences


def test_no_fence_passthrough() -> None:
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_strip_json_lang_fence() -> None:
    assert strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_bare_fence() -> None:
    assert strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_handles_whitespace() -> None:
    assert strip_json_fences('   ```json\n{"a": 1}\n```   ') == '{"a": 1}'


def test_no_trailing_fence() -> None:
    # vendor sometimes only emits the opening fence
    assert strip_json_fences('```json\n{"a": 1}') == '{"a": 1}'
