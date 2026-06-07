"""Pytest configuration shared across the suite.

Goal (M1): unit tests run with no external services. Integration tests
that need Postgres/Redis live in tests/integration and are introduced in
M2 with testcontainers.
"""

from __future__ import annotations

import os

import pytest

# Make sure unit tests don't accidentally hit a real database / Redis.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "unit-test-stub")
os.environ.setdefault("DEV_MODE", "false")


@pytest.fixture
def fixed_now():
    from datetime import UTC, datetime

    return datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)
