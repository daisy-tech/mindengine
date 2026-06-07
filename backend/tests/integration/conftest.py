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
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import (
    MemoryRepos,
    current_user,
    get_memory_repos,
    get_session,
    get_settings_dep,
)
from app.config import Settings
from app.infra.auth import JwtCodec, create_default_hasher
from app.main import create_app
from app.services.auth import AuthService
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeDeprecationRepo,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeProfileRepo,
    FakeRelationshipRepo,
    FakeUserStore,
)


@pytest.fixture
def harness():
    users = FakeUserStore()
    settings = Settings(
        jwt_secret="test-secret-do-not-use-in-prod",
        dev_mode=False,
        task_dispatcher="in_memory",
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
        # Per-user fake repo registry — same instance for every request so
        # tests can pre-seed and inspect data across calls.
        self._repos: dict[str, MemoryRepos] = {}

    def repos_for(self, user_id: str) -> MemoryRepos:
        if user_id not in self._repos:
            self._repos[user_id] = MemoryRepos(
                profile=FakeProfileRepo(user_id=user_id),
                event=FakeEventRepo(user_id=user_id),
                episodic=FakeEpisodicRepo(user_id=user_id),
                relationship=FakeRelationshipRepo(user_id=user_id),
                banned=FakeBannedEntityRepo(user_id=user_id),
                deprecation=FakeDeprecationRepo(user_id=user_id),
            )
        return self._repos[user_id]


@pytest.fixture
def client(harness: SimpleHarness, monkeypatch) -> TestClient:
    app = create_app()
    app.state.jwt = harness.jwt
    app.state.settings = harness.settings

    async def _stub_session() -> AsyncIterator[Any]:
        yield _NoopSession()

    app.dependency_overrides[get_settings_dep] = lambda: harness.settings
    app.dependency_overrides[get_session] = _stub_session

    # Auth router constructs UserRepo from the session; replace the
    # whole AuthService with our in-memory one.
    monkeypatch.setattr(
        "app.api.auth._build_service",
        lambda session, settings: harness.auth_service,
    )

    # api.memory + api.conversations rely on the MemoryRepos bundle —
    # override with the per-user fake bundle from harness.repos_for(...).
    # NOTE: ``from __future__ import annotations`` makes the ``CurrentUserId``
    # alias a forward-ref string at runtime, which FastAPI mis-parses as a
    # query param. Use a plain ``Depends(current_user)`` here instead.
    def _fake_repos_provider(
        user_id: str = Depends(current_user),
    ) -> MemoryRepos:
        return harness.repos_for(user_id)

    app.dependency_overrides[get_memory_repos] = _fake_repos_provider
    return TestClient(app)


class _NoopSession:
    """Stand-in for AsyncSession when integration tests don't need a real DB.

    Endpoints under test only call ``commit()`` and possibly ``flush()`` on
    it; the fake repos do their own state mutation, so these are pure
    no-ops here.
    """

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dev_harness():
    """Same harness, but dev_mode=True so X-Dev-User-Id header bypass works."""
    users = FakeUserStore()
    settings = Settings(
        jwt_secret="test-secret-do-not-use-in-prod",
        dev_mode=True,
        task_dispatcher="in_memory",
    )
    jwt = JwtCodec(secret=settings.jwt_secret, default_ttl=timedelta(minutes=10))
    hasher = create_default_hasher()
    return SimpleHarness(
        users=users,
        settings=settings,
        jwt=jwt,
        hasher=hasher,
        auth_service=AuthService(users=users, hasher=hasher, jwt=jwt),
    )


@pytest.fixture
def dev_client(dev_harness: SimpleHarness, monkeypatch) -> TestClient:
    """TestClient wired to ``dev_harness`` (dev_mode=True)."""
    app = create_app()
    app.state.jwt = dev_harness.jwt
    app.state.settings = dev_harness.settings

    async def _stub_session() -> AsyncIterator[Any]:
        yield _NoopSession()

    app.dependency_overrides[get_settings_dep] = lambda: dev_harness.settings
    app.dependency_overrides[get_session] = _stub_session
    monkeypatch.setattr(
        "app.api.auth._build_service",
        lambda session, settings: dev_harness.auth_service,
    )

    def _fake_repos_provider(
        user_id: str = Depends(current_user),
    ) -> MemoryRepos:
        return dev_harness.repos_for(user_id)

    app.dependency_overrides[get_memory_repos] = _fake_repos_provider
    return TestClient(app)


def dev_headers(user_id: str) -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}
