"""Tests that FastAPI app boots and /healthz returns 200 (no DB needed)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def app_client() -> TestClient:
    # Skip the lifespan (which would build a DB engine) by constructing a
    # fresh app and not entering the lifespan context. TestClient with
    # `raise_server_exceptions=True` works without lifespan unless we
    # rely on app.state.* — /healthz doesn't.
    app = create_app()
    return TestClient(app)


def test_healthz_ok(app_client: TestClient) -> None:
    resp = app_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_app_metadata() -> None:
    app = create_app()
    assert app.title == "MindEngine API"
    assert app.version == "0.1.0"


def test_routes_include_healthz() -> None:
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/healthz" in paths
    assert "/readyz" in paths
