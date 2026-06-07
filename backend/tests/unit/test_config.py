"""Tests for app.config.Settings."""

from __future__ import annotations

import importlib

from app.config import get_settings


def test_defaults_are_dev_safe() -> None:
    s = get_settings()
    # Lesson 9.4 / D6: dev-mode features must be explicitly opted into.
    assert s.dev_mode is False
    assert s.allow_destructive_dev is False
    assert s.enable_thinking is False  # lesson 7.1


def test_get_settings_caches() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_runtime_kind_choices() -> None:
    s = get_settings()
    assert s.runtime_kind in ("web", "worker")


def test_settings_module_reload_safe() -> None:
    # Reload shouldn't blow up — useful when changing env in tests.
    import app.config

    importlib.reload(app.config)
