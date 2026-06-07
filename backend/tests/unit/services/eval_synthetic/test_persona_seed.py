"""Tests for ``seed_eval_persona`` (repo-Protocol-driven)."""

from __future__ import annotations

import pytest

from app.domain.memory import (
    BasicInfo,
    Profile,
    Relationship,
)
from app.services.eval_synthetic.persona_seed import (
    PersonaSeed,
    default_seed,
    seed_eval_persona,
)
from tests.unit.services._fakes import (
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


@pytest.mark.asyncio
async def test_seed_default_inserts_canonical_persona():
    user_id = "eval-bot"
    profile = FakeProfileRepo(user_id=user_id)
    event = FakeEventRepo(user_id=user_id)
    episodic = FakeEpisodicRepo(user_id=user_id)
    rel = FakeRelationshipRepo(user_id=user_id)

    summary = await seed_eval_persona(
        user_id=user_id,
        profile_repo=profile,
        event_repo=event,
        episodic_repo=episodic,
        relationship_repo=rel,
    )
    assert summary.user_id == user_id
    assert summary.profile_set is True
    assert summary.events_inserted == 2
    assert summary.episodic_inserted == 3
    assert summary.relationships_upserted == 1


@pytest.mark.asyncio
async def test_seed_rewrites_user_id_when_mismatch():
    user_id = "alice"
    custom = PersonaSeed(
        profile=Profile(user_id="someone-else", basic=BasicInfo(name="X")),
        events=[],
        episodic=["fact 1"],
        relationships=[
            Relationship(
                id="r1", user_id="someone-else", name="李娜",
                role="妻子", attributes={}, via=None, status="active",
                created_at=None,
            )
        ],
    )
    profile = FakeProfileRepo(user_id=user_id)
    event = FakeEventRepo(user_id=user_id)
    episodic = FakeEpisodicRepo(user_id=user_id)
    rel = FakeRelationshipRepo(user_id=user_id)
    await seed_eval_persona(
        user_id=user_id,
        profile_repo=profile,
        event_repo=event,
        episodic_repo=episodic,
        relationship_repo=rel,
        seed=custom,
    )
    assert profile.profile is not None
    assert profile.profile.user_id == user_id
    assert all(r.user_id == user_id for r in rel.rows)


@pytest.mark.asyncio
async def test_seed_skips_blank_episodic_strings():
    user_id = "alice"
    plan = PersonaSeed(profile=None, events=[], episodic=["", "  ", "real"], relationships=[])
    profile = FakeProfileRepo(user_id=user_id)
    event = FakeEventRepo(user_id=user_id)
    episodic = FakeEpisodicRepo(user_id=user_id)
    rel = FakeRelationshipRepo(user_id=user_id)
    summary = await seed_eval_persona(
        user_id=user_id,
        profile_repo=profile,
        event_repo=event,
        episodic_repo=episodic,
        relationship_repo=rel,
        seed=plan,
    )
    assert summary.episodic_inserted == 1


def test_default_seed_uses_provided_user_id():
    seed = default_seed("u-x")
    assert seed.profile is not None
    assert seed.profile.user_id == "u-x"
    assert all(ev.user_id == "u-x" for ev in seed.events)
    assert all(rel.user_id == "u-x" for rel in seed.relationships)
