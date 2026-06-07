"""Unit tests for correction.applier (cross-layer mutation + audit)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.correction import (
    CorrectionCandidate,
    CorrectionJudgement,
)
from app.domain.memory import (
    BasicInfo,
    EpisodicMemory,
    Event,
    Profile,
)
from app.services.correction.applier import CorrectionApplier
from app.services.correction.judge import JudgementOutcome
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeDeprecationRepo,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeProfileRepo,
)


def _applier(
    profile: FakeProfileRepo,
    event: FakeEventRepo,
    episodic: FakeEpisodicRepo,
    banned: FakeBannedEntityRepo,
    deprecation: FakeDeprecationRepo,
) -> CorrectionApplier:
    return CorrectionApplier(
        profile_repo=profile,
        event_repo=event,
        episodic_repo=episodic,
        banned_repo=banned,
        deprecation_repo=deprecation,
    )


def _outcome(
    cand: CorrectionCandidate,
    *,
    action: str = "deprecate",
    confidence: float = 0.9,
    new_text: str | None = None,
) -> JudgementOutcome:
    return JudgementOutcome(
        candidate=cand,
        judgement=CorrectionJudgement(
            action=action, confidence=confidence, reason="ok", new_text=new_text
        ),
    )


@pytest.mark.asyncio
async def test_apply_deprecate_episodic_soft_deletes_and_logs():
    profile = FakeProfileRepo(user_id="u1")
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    episodic.rows.append(
        EpisodicMemory(id="m1", user_id="u1", text="老家在岳西", source="x")
    )
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(
                    source="episodic", ref_id="m1", text="老家在岳西"
                )
            )
        ],
        banned=["岳西"],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.episodic_soft_deleted == 1
    assert episodic.rows[0].status == "deprecated"
    assert out.deprecations_inserted == 1
    assert out.banned_inserted == 1
    assert dep.deprecated_episodic == {"m1"}


@pytest.mark.asyncio
async def test_apply_audit_only_does_not_touch_data():
    profile = FakeProfileRepo(user_id="u1")
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    episodic.rows.append(
        EpisodicMemory(id="m1", user_id="u1", text="老家在岳西", source="x")
    )
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(
                    source="episodic", ref_id="m1", text="x"
                ),
                action="audit_only",
                confidence=0.4,
            )
        ],
        banned=[],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.episodic_soft_deleted == 0
    assert episodic.rows[0].status == "active"
    assert out.audit_only == 1


@pytest.mark.asyncio
async def test_apply_event_deprecate():
    profile = FakeProfileRepo(user_id="u1")
    event = FakeEventRepo(user_id="u1")
    event.rows.append(
        Event(
            id="e1", user_id="u1", type="experience",
            title="搬到岳西", content="2020", occurred_at=None,
            status="active", source_message_id=None, created_at=datetime.now(UTC),
        )
    )
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(source="event", ref_id="e1", text="x")
            )
        ],
        banned=[],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.event_soft_deleted == 1
    assert event.rows[0].status == "deprecated"


@pytest.mark.asyncio
async def test_apply_profile_update_replaces_field():
    profile = FakeProfileRepo(user_id="u1")
    profile.profile = Profile(
        user_id="u1", basic=BasicInfo(name="张三", location="岳西")
    )
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(
                    source="profile",
                    ref_id="basic.location",
                    text="basic.location=岳西",
                ),
                action="update",
                new_text="怀宁",
            )
        ],
        banned=["岳西"],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对，是怀宁",
    )
    assert out.profile_fields_patched == 1
    assert profile.profile is not None
    assert profile.profile.basic.location == "怀宁"
    assert len(profile.profile.user_corrections) == 1


@pytest.mark.asyncio
async def test_apply_profile_deprecate_clears_field():
    profile = FakeProfileRepo(user_id="u1")
    profile.profile = Profile(
        user_id="u1", basic=BasicInfo(location="岳西")
    )
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(
                    source="profile", ref_id="basic.location", text="x"
                )
            )
        ],
        banned=[],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.profile_fields_patched == 1
    assert profile.profile is not None
    assert profile.profile.basic.location is None


@pytest.mark.asyncio
async def test_apply_profile_interest_remove():
    profile = FakeProfileRepo(user_id="u1")
    profile.profile = Profile(
        user_id="u1", basic=BasicInfo(), interests=["弹钢琴", "下棋"]
    )
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(
                    source="profile",
                    ref_id="interests[0]",
                    text="弹钢琴",
                ),
            )
        ],
        banned=[],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.profile_fields_patched == 1
    assert profile.profile is not None
    assert profile.profile.interests == ["下棋"]


@pytest.mark.asyncio
async def test_apply_entity_source_audits_only_no_state_change():
    profile = FakeProfileRepo(user_id="u1")
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out = await a.apply(
        outcomes=[
            _outcome(
                CorrectionCandidate(source="entity", ref_id="rel-1", text="x")
            )
        ],
        banned=["小鹏"],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="不对",
    )
    assert out.audit_only == 1
    assert out.deprecations_inserted == 1
    assert out.banned_inserted == 1


@pytest.mark.asyncio
async def test_apply_dedupes_banned_entities_via_repo():
    profile = FakeProfileRepo(user_id="u1")
    event = FakeEventRepo(user_id="u1")
    episodic = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    dep = FakeDeprecationRepo(user_id="u1")
    a = _applier(profile, event, episodic, banned, dep)
    out1 = await a.apply(
        outcomes=[],
        banned=["岳西"],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t1",
        user_correction_text="x",
    )
    out2 = await a.apply(
        outcomes=[],
        banned=["岳西"],
        user_id="u1",
        conversation_id="c1",
        correction_message_id="t2",
        user_correction_text="x",
    )
    assert out1.banned_inserted == 1
    assert out2.banned_inserted == 0  # dedup at repo
