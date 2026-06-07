"""Integration-test fixtures.

We can't run a real Postgres + pgvector inside the dev sandbox (no Docker
daemon, no testcontainers). Instead these tests cover the *API surface*:
auth flow, route registration, dev-mode auth bypass, and 401 enforcement.

The full chat persistence path is exercised by the unit tests in
``tests/unit/services/test_chat_orchestrator.py`` against in-memory fakes
that satisfy the same Protocols.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_session, get_settings_dep
from app.config import Settings
from app.infra.auth import JwtCodec, create_default_hasher
from app.main import create_app
from app.services.auth import AuthService
from tests.unit.services._fakes import FakeUserStore


@pytest.fixture
def harness():
    users = FakeUserStore()
    settings = Settings(
        jwt_secret="test-secret-do-not-use-in-prod",
        dev_mode=False,
    )
    jwt = JwtCodec(
        secret=settings.jwt_secret,
        default_ttl=timedelta(minutes=10),
    )
    hasher = create_default_hasher()
    auth_service = AuthService(users=users, hasher=hasher, jwt=jwt)
    return SimpleHarness(
        users=users,
        settings=settings,
        jwt=jwt,
        hasher=hasher,
        auth_service=auth_service,
    )


class SimpleHarness:
    def __init__(self, *, users, settings, jwt, hasher, auth_service):
        self.users = users
        self.settings = settings
        self.jwt = jwt
        self.hasher = hasher
        self.auth_service = auth_service


@pytest.fixture
def client(harness: SimpleHarness, monkeypatch) -> TestClient:
    app = create_app()
    app.state.jwt = harness.jwt
    app.state.settings = harness.settings

    async def _stub_session() -> AsyncIterator[Any]:
        yield None

    app.dependency_overrides[get_settings_dep] = lambda: harness.settings
    app.dependency_overrides[get_session] = _stub_session

    # Auth router constructs UserRepo from the session; replace the
    # whole AuthService with our in-memory one.
    monkeypatch.setattr(
        "app.api.auth._build_service",
        lambda session, settings: harness.auth_service,
    )
    return TestClient(app)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
