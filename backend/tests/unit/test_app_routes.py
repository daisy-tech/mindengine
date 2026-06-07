"""Smoke tests for the FastAPI route registry — no DB required."""

from __future__ import annotations

from app.main import create_app


def test_app_registers_expected_routes() -> None:
    app = create_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    expected = {
        "/healthz",
        "/readyz",
        "/auth/register",
        "/auth/login",
        "/auth/me",
        "/api/conversations",
        "/api/conversations/{conversation_id}/messages",
        "/api/chat",
    }
    missing = expected - paths
    assert not missing, f"missing routes: {missing}"


def test_openapi_schema_renders() -> None:
    app = create_app()
    schema = app.openapi()
    assert schema["info"]["title"]
    assert "/api/chat" in schema["paths"]
    assert "/auth/register" in schema["paths"]
