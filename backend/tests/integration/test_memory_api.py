"""Memory inspection API — integration tests with fake repos."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.correction import MemoryDeprecation
from app.domain.memory import EpisodicMemory, Event, Profile, Relationship
from tests.integration.conftest import SimpleHarness, dev_headers

# ─────────────────────────────────────────────────────────────────
# 401 enforcement
# ─────────────────────────────────────────────────────────────────


def test_memory_api_requires_auth(client: TestClient) -> None:
    for path in [
        "/api/memory/profile",
        "/api/memory/events",
        "/api/memory/episodic",
        "/api/memory/relationships",
        "/api/memory/banned-entities",
        "/api/memory/deprecations",
    ]:
        r = client.get(path)
        assert r.status_code == 401, path


# ─────────────────────────────────────────────────────────────────
# profile
# ─────────────────────────────────────────────────────────────────


def test_get_profile_returns_null_when_missing(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    r = dev_client.get("/api/memory/profile", headers=dev_headers("u1"))
    assert r.status_code == 200, r.json()
    assert r.json() is None


def test_get_profile_returns_existing(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    p = Profile(user_id="u1", interests=["跑步"])
    p.basic.name = "张三"
    repos.profile.profile = p

    r = dev_client.get("/api/memory/profile", headers=dev_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u1"
    assert body["basic"]["name"] == "张三"
    assert body["interests"] == ["跑步"]


def test_patch_profile_merges_and_records_correction(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    r = dev_client.patch(
        "/api/memory/profile",
        json={"basic": {"name": "张三"}, "interests": ["跑步", "围棋"]},
        headers=dev_headers("u2"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["basic"]["name"] == "张三"
    assert body["interests"] == ["跑步", "围棋"]
    # First write: None → "张三" gets logged in user_corrections.
    assert len(body["user_corrections"]) == 1
    assert body["user_corrections"][0]["field_path"] == "basic.name"
    assert body["user_corrections"][0]["old_value"] is None

    # Second PATCH overwriting an existing override-field → adds another row.
    r2 = dev_client.patch(
        "/api/memory/profile",
        json={"basic": {"name": "张三丰"}},
        headers=dev_headers("u2"),
    )
    assert r2.status_code == 200
    assert r2.json()["basic"]["name"] == "张三丰"
    corrections = r2.json()["user_corrections"]
    assert len(corrections) == 2
    assert corrections[-1]["field_path"] == "basic.name"
    assert corrections[-1]["old_value"] == "张三"
    assert corrections[-1]["new_value"] == "张三丰"


def test_patch_profile_rejects_empty_body(dev_client: TestClient) -> None:
    r = dev_client.patch(
        "/api/memory/profile", json={}, headers=dev_headers("u3")
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────
# events
# ─────────────────────────────────────────────────────────────────


def test_list_events_returns_recent(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    repos.event.rows = [
        Event(id="e1", user_id="u1", type="experience", title="t1", content="c1"),
        Event(id="e2", user_id="u1", type="plan", title="t2", content="c2"),
    ]
    r = dev_client.get("/api/memory/events", headers=dev_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    assert {row["id"] for row in body} == {"e1", "e2"}


def test_list_events_filters_by_type(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    repos.event.rows = [
        Event(id="e1", user_id="u1", type="experience", title="t1", content="c1"),
        Event(id="e2", user_id="u1", type="plan", title="t2", content="c2"),
    ]
    r = dev_client.get(
        "/api/memory/events?type=plan", headers=dev_headers("u1")
    )
    assert r.status_code == 200
    body = r.json()
    assert {row["id"] for row in body} == {"e2"}


def test_list_events_validates_limit(dev_client: TestClient) -> None:
    r = dev_client.get(
        "/api/memory/events?limit=999", headers=dev_headers("u1")
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────
# episodic + delete
# ─────────────────────────────────────────────────────────────────


def test_list_and_delete_episodic(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    repos.episodic.rows = [
        EpisodicMemory(id="m1", user_id="u1", text="用户喜欢台球"),
        EpisodicMemory(id="m2", user_id="u1", text="用户喜欢茶"),
    ]
    r = dev_client.get("/api/memory/episodic", headers=dev_headers("u1"))
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = dev_client.delete(
        "/api/memory/episodic/m1?reason=test",
        headers=dev_headers("u1"),
    )
    assert r.status_code == 204

    # Soft-deleted: list_recent now returns one
    r = dev_client.get("/api/memory/episodic", headers=dev_headers("u1"))
    assert {row["id"] for row in r.json()} == {"m2"}

    # Audit row appended.
    deps = dev_client.get(
        "/api/memory/deprecations", headers=dev_headers("u1")
    )
    assert deps.status_code == 200
    rows = deps.json()
    assert any(d["ref_id"] == "m1" and d["source"] == "episodic" for d in rows)


# ─────────────────────────────────────────────────────────────────
# relationships
# ─────────────────────────────────────────────────────────────────


def test_list_relationships(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    repos.relationship.rows = [
        Relationship(id="r1", user_id="u1", name="张三", role="妻子"),
        Relationship(id="r2", user_id="u1", name="小宝", role="儿子"),
    ]
    r = dev_client.get("/api/memory/relationships", headers=dev_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    names = {row["name"] for row in body}
    assert names == {"张三", "小宝"}


# ─────────────────────────────────────────────────────────────────
# banned entities
# ─────────────────────────────────────────────────────────────────


def test_post_and_list_banned_entities(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    r = dev_client.post(
        "/api/memory/banned-entities",
        json={"entities": ["岳西", "小明", " 重复 ", "重复"], "reason": "stale"},
        headers=dev_headers("u1"),
    )
    assert r.status_code == 201
    assert r.json()["inserted"] == 3  # set dedup → 3 unique

    r = dev_client.get(
        "/api/memory/banned-entities", headers=dev_headers("u1")
    )
    assert r.status_code == 200
    entities = {row["entity"] for row in r.json()}
    assert entities == {"岳西", "小明", "重复"}


def test_post_banned_rejects_only_blank(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    r = dev_client.post(
        "/api/memory/banned-entities",
        json={"entities": ["", "   "]},
        headers=dev_headers("u1"),
    )
    # Pydantic will reject min_length on entities itself? Let's check —
    # entities has min_length=1 (at least one element); blank strings get
    # filtered server-side. That hits our 400.
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────
# deprecations
# ─────────────────────────────────────────────────────────────────


def test_list_deprecations_returns_seeded(
    dev_client: TestClient, dev_harness: SimpleHarness
) -> None:
    repos = dev_harness.repos_for("u1")
    now = datetime.now(UTC)
    repos.deprecation.rows = [
        MemoryDeprecation(
            user_id="u1",
            source="episodic",
            ref_id="m1",
            reason="changed",
            action="deprecate",
            deprecated_at=now,
        )
    ]
    r = dev_client.get("/api/memory/deprecations", headers=dev_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    assert body[0]["ref_id"] == "m1"
    assert body[0]["action"] == "deprecate"
