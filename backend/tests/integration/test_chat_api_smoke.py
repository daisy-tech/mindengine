"""Chat-endpoint smoke tests via TestClient.

We DON'T do a full chat round-trip here (would require a real DB session
or a much larger override harness). What we DO verify:

- /api/chat without auth ⇒ 401
- /api/chat schema validation ⇒ 422 on bad body
- /api/conversations without auth ⇒ 401
- the routes are reachable and the auth dep fires before the handler
"""

from __future__ import annotations


def test_chat_requires_auth(client) -> None:
    r = client.post(
        "/api/chat",
        json={"conversation_id": "c1", "message": "hi"},
    )
    assert r.status_code == 401


def test_chat_rejects_empty_message(client) -> None:
    # Even an authenticated request must pass body validation.
    reg = client.post(
        "/auth/register",
        json={"email": "u1@example.com", "password": "hunter22-strong"},
    )
    token = reg.json()["access_token"]
    r = client.post(
        "/api/chat",
        json={"conversation_id": "c1", "message": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_conversations_requires_auth(client) -> None:
    r = client.get("/api/conversations")
    assert r.status_code == 401
    r = client.get("/api/conversations/c1/messages")
    assert r.status_code == 401


def test_health_endpoints_dont_need_auth(client) -> None:
    assert client.get("/healthz").status_code == 200
    # /readyz pings the DB which our stub doesn't provide; the call may
    # 503 cleanly without going through auth.
    r = client.get("/readyz")
    assert r.status_code in (200, 503)
