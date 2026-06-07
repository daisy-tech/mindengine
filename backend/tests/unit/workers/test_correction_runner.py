"""Unit tests for ``run_correction_cleanup`` (the async worker runner)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.llm import LLMRole
from app.domain.memory import EpisodicMemory
from app.infra.llm.mock_client import MockLLMClient
from app.services.correction import (
    BannedExtractor,
    CorrectionApplier,
    CorrectionCandidateSearcher,
    CorrectionJudge,
    CorrectionTargetExtractor,
)
from app.workers.runners import run_correction_cleanup
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeDeprecationRepo,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeMessageRepo,
    FakeMessageRow,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


def _llm() -> MockLLMClient:
    return MockLLMClient(role=LLMRole.CORRECTION, model="mock")


def _seed_messages(repo: FakeMessageRepo, *, conv: str, user_id: str):
    base = datetime.now(UTC) - timedelta(minutes=5)
    repo.rows.append(
        FakeMessageRow(
            id="u1", conversation_id=conv, user_id=user_id,
            role="user", content="我老家在湖南怀化",
            meta_json=None, created_at=base,
        )
    )
    repo.rows.append(
        FakeMessageRow(
            id="a1", conversation_id=conv, user_id=user_id,
            role="assistant", content="哦，怀化的米粉很有名。",
            meta_json={}, created_at=base + timedelta(seconds=1),
        )
    )
    repo.rows.append(
        FakeMessageRow(
            id="u2", conversation_id=conv, user_id=user_id,
            role="user", content="不对，是怀宁，邻近安徽岳西",
            meta_json=None, created_at=base + timedelta(seconds=2),
        )
    )


@pytest.mark.asyncio
async def test_correction_runner_skips_when_message_missing():
    repo = FakeMessageRepo(user_id="u1")
    out = await run_correction_cleanup(
        target_extractor=CorrectionTargetExtractor(llm=_llm()),
        searcher=_searcher_for_user("u1"),
        judge=CorrectionJudge(llm=_llm()),
        banned_extractor=BannedExtractor(llm=_llm()),
        applier=_applier_for_user("u1"),
        message_repo=repo,
        user_id="u1",
        conversation_id="c1",
        message_id="missing",
    )
    assert out["status"] == "skipped"


def _searcher_for_user(user_id: str) -> CorrectionCandidateSearcher:
    return CorrectionCandidateSearcher(
        profile_repo=FakeProfileRepo(user_id=user_id),
        event_repo=FakeEventRepo(user_id=user_id),
        episodic_repo=FakeEpisodicRepo(user_id=user_id),
        relationship_repo=FakeRelationshipRepo(user_id=user_id),
    )


def _applier_for_user(user_id: str) -> CorrectionApplier:
    return CorrectionApplier(
        profile_repo=FakeProfileRepo(user_id=user_id),
        event_repo=FakeEventRepo(user_id=user_id),
        episodic_repo=FakeEpisodicRepo(user_id=user_id),
        banned_repo=FakeBannedEntityRepo(user_id=user_id),
        deprecation_repo=FakeDeprecationRepo(user_id=user_id),
    )


@pytest.mark.asyncio
async def test_correction_runner_full_pipeline_e2e_with_episodic_match():
    user_id = "u1"
    msg_repo = FakeMessageRepo(user_id=user_id)
    _seed_messages(msg_repo, conv="c1", user_id=user_id)

    profile = FakeProfileRepo(user_id=user_id)
    event = FakeEventRepo(user_id=user_id)
    episodic = FakeEpisodicRepo(user_id=user_id)
    episodic.rows.append(
        EpisodicMemory(id="m1", user_id=user_id, text="老家在湖南怀化", source="x")
    )
    rel = FakeRelationshipRepo(user_id=user_id)
    banned = FakeBannedEntityRepo(user_id=user_id)
    dep = FakeDeprecationRepo(user_id=user_id)

    target_llm = _llm().queue_json(
        {
            "targets": [
                {"ref": "怀化", "verb": "不是", "correct": "怀宁"}
            ]
        }
    )
    judge_llm = _llm().queue_json(
        {"action": "deprecate", "confidence": 0.95, "reason": "明确"}
    )
    banned_llm = _llm().queue_json({"entities": ["怀化"]})

    out = await run_correction_cleanup(
        target_extractor=CorrectionTargetExtractor(llm=target_llm),
        searcher=CorrectionCandidateSearcher(
            profile_repo=profile,
            event_repo=event,
            episodic_repo=episodic,
            relationship_repo=rel,
        ),
        judge=CorrectionJudge(llm=judge_llm, confidence_threshold=0.7),
        banned_extractor=BannedExtractor(llm=banned_llm),
        applier=CorrectionApplier(
            profile_repo=profile,
            event_repo=event,
            episodic_repo=episodic,
            banned_repo=banned,
            deprecation_repo=dep,
        ),
        message_repo=msg_repo,
        user_id=user_id,
        conversation_id="c1",
        message_id="u2",
    )
    assert out["status"] == "ok"
    assert out["targets"] == 1
    assert out["candidates"] >= 1
    assert episodic.rows[0].status == "deprecated"
    assert any(b.entity == "怀化" for b in banned.rows)


@pytest.mark.asyncio
async def test_correction_runner_no_targets_returns_zero():
    user_id = "u1"
    msg_repo = FakeMessageRepo(user_id=user_id)
    _seed_messages(msg_repo, conv="c1", user_id=user_id)

    target_llm = _llm().queue_json({"targets": []})

    out = await run_correction_cleanup(
        target_extractor=CorrectionTargetExtractor(llm=target_llm),
        searcher=_searcher_for_user(user_id),
        judge=CorrectionJudge(llm=_llm()),
        banned_extractor=BannedExtractor(llm=_llm()),
        applier=_applier_for_user(user_id),
        message_repo=msg_repo,
        user_id=user_id,
        conversation_id="c1",
        message_id="u2",
    )
    assert out["targets"] == 0
    assert out["applied"] == 0
