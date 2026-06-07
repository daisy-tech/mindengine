"""Worker runners — tested with fake repos + MockLLMClient."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.correction import BannedEntity
from app.domain.llm import LLMRole
from app.domain.memory import Profile, Relationship
from app.infra.llm.mock_client import MockLLMClient
from app.services.memory_extract import (
    EpisodicExtractor,
    EventExtractor,
    ProfileExtractor,
    RelationshipExtractor,
)
from app.workers.runners import (
    run_extract_episodic,
    run_extract_event,
    run_extract_profile,
    run_extract_relationship,
)
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeMessageRepo,
    FakeMessageRow,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


def _seed_msg(
    repo: FakeMessageRepo,
    *,
    conversation_id: str,
    message_id: str,
    content: str,
    user_id: str,
    role: str = "user",
) -> None:
    repo.rows.append(
        FakeMessageRow(
            id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            content=content,
            meta_json=None,
            created_at=datetime.now(UTC),
        )
    )


# ─────────────────────────────────────────────────────────────────
# Episodic
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_extract_episodic_skips_when_message_missing():
    msg_repo = FakeMessageRepo(user_id="u1")
    epi_repo = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json({"facts": ["x"]})
    )

    result = await run_extract_episodic(
        extractor=extractor,
        message_repo=msg_repo,
        episodic_repo=epi_repo,
        banned_repo=banned,
        conversation_id="c1",
        message_id="m_missing",
    )
    assert result == {"status": "skipped", "reason": "message_not_found"}
    assert epi_repo.rows == []


@pytest.mark.asyncio
async def test_run_extract_episodic_inserts_facts_and_filters_banned():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="我儿子小鹏喜欢台球", user_id="u1")
    banned = FakeBannedEntityRepo(
        user_id="u1",
        rows=[BannedEntity(user_id="u1", entity="小鹏", reason="changed")],
    )
    epi_repo = FakeEpisodicRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {"facts": ["用户的儿子小鹏喜欢台球", "用户每周和儿子打台球"]}
        )
    )

    result = await run_extract_episodic(
        extractor=extractor,
        message_repo=msg_repo,
        episodic_repo=epi_repo,
        banned_repo=banned,
        conversation_id="c1",
        message_id="m1",
    )
    assert result["status"] == "ok"
    assert result["inserted"] == 1
    assert result["skipped_banned"] == 1
    assert len(epi_repo.rows) == 1
    assert "台球" in epi_repo.rows[0].text


@pytest.mark.asyncio
async def test_run_extract_episodic_returns_zero_when_extractor_empty():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="嗯", user_id="u1")
    epi_repo = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json({"facts": []})
    )

    result = await run_extract_episodic(
        extractor=extractor,
        message_repo=msg_repo,
        episodic_repo=epi_repo,
        banned_repo=banned,
        conversation_id="c1",
        message_id="m1",
    )
    assert result == {"status": "ok", "inserted": 0}
    assert epi_repo.rows == []


# ─────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_extract_profile_creates_and_merges():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="我叫张三，喜欢跑步", user_id="u1")
    profile_repo = FakeProfileRepo(user_id="u1")
    extractor = ProfileExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {"basic": {"name": "张三"}, "interests": ["跑步"]}
        )
    )

    result = await run_extract_profile(
        extractor=extractor,
        message_repo=msg_repo,
        profile_repo=profile_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    assert result["status"] == "ok"
    assert result["fields"] >= 2
    saved = profile_repo.profile
    assert saved is not None
    assert saved.basic.name == "张三"
    assert saved.interests == ["跑步"]


@pytest.mark.asyncio
async def test_run_extract_profile_merges_into_existing():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="搬到上海了，喜欢打篮球", user_id="u1")
    existing = Profile(user_id="u1", interests=["跑步"])
    existing.basic.name = "张三"
    profile_repo = FakeProfileRepo(user_id="u1", profile=existing)
    extractor = ProfileExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {"basic": {"location": "上海"}, "interests": ["篮球"]}
        )
    )

    await run_extract_profile(
        extractor=extractor,
        message_repo=msg_repo,
        profile_repo=profile_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    saved = profile_repo.profile
    assert saved is not None
    assert saved.basic.name == "张三"
    assert saved.basic.location == "上海"
    assert saved.interests == ["跑步", "篮球"]


@pytest.mark.asyncio
async def test_run_extract_profile_no_op_when_extractor_returns_empty():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="今天天气不错", user_id="u1")
    profile_repo = FakeProfileRepo(user_id="u1")
    extractor = ProfileExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json({})
    )

    result = await run_extract_profile(
        extractor=extractor,
        message_repo=msg_repo,
        profile_repo=profile_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    assert result == {"status": "ok", "fields": 0}
    assert profile_repo.profile is None


# ─────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_extract_event_inserts_typed_events():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="周末和儿子吃饭，下周一面试 X 公司", user_id="u1")
    event_repo = FakeEventRepo(user_id="u1")
    extractor = EventExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "events": [
                    {"type": "experience", "title": "和儿子吃饭", "content": "周末"},
                    {"type": "plan", "title": "面试", "content": "下周一面 X 公司"},
                ]
            }
        )
    )

    result = await run_extract_event(
        extractor=extractor,
        message_repo=msg_repo,
        event_repo=event_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    assert result["status"] == "ok"
    assert result["inserted"] == 2
    assert {e.type for e in event_repo.rows} == {"experience", "plan"}
    assert all(e.user_id == "u1" for e in event_repo.rows)
    assert all(e.source_message_id == "m1" for e in event_repo.rows)


@pytest.mark.asyncio
async def test_run_extract_event_skips_when_message_missing():
    msg_repo = FakeMessageRepo(user_id="u1")
    event_repo = FakeEventRepo(user_id="u1")
    extractor = EventExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json({"events": []})
    )

    result = await run_extract_event(
        extractor=extractor,
        message_repo=msg_repo,
        event_repo=event_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m_missing",
    )
    assert result["status"] == "skipped"
    assert event_repo.rows == []


# ─────────────────────────────────────────────────────────────────
# Relationship
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_extract_relationship_creates_new():
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="我老婆张三是医生", user_id="u1")
    rel_repo = FakeRelationshipRepo(user_id="u1")
    extractor = RelationshipExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "relationships": [
                    {"name": "张三", "role": "妻子", "attributes": {"职业": "医生"}}
                ]
            }
        )
    )

    result = await run_extract_relationship(
        extractor=extractor,
        message_repo=msg_repo,
        relationship_repo=rel_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    assert result["status"] == "ok"
    assert result["upserted"] == 1
    assert len(rel_repo.rows) == 1
    rel = rel_repo.rows[0]
    assert rel.name == "张三"
    assert rel.role == "妻子"
    assert rel.attributes["职业"] == "医生"
    assert rel.via is None


@pytest.mark.asyncio
async def test_run_extract_relationship_merges_attributes_for_existing():
    existing = Relationship(
        id="r1",
        user_id="u1",
        name="张三",
        role="妻子",
        attributes={"职业": "医生"},
    )
    rel_repo = FakeRelationshipRepo(user_id="u1", rows=[existing])
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="老婆 35 岁了", user_id="u1")
    extractor = RelationshipExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "relationships": [
                    {"name": "张三", "role": "妻子", "attributes": {"年龄": "35"}}
                ]
            }
        )
    )

    await run_extract_relationship(
        extractor=extractor,
        message_repo=msg_repo,
        relationship_repo=rel_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    assert len(rel_repo.rows) == 1
    assert rel_repo.rows[0].attributes == {"职业": "医生", "年龄": "35"}


@pytest.mark.asyncio
async def test_run_extract_relationship_resolves_via_by_name():
    """via_name on the candidate is resolved to the existing rel id."""
    existing = Relationship(id="r-wife", user_id="u1", name="张三", role="妻子")
    rel_repo = FakeRelationshipRepo(user_id="u1", rows=[existing])
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="她妈妈也住在上海", user_id="u1")
    extractor = RelationshipExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "relationships": [
                    {"name": "李娟", "role": "丈母娘", "via_name": "张三"},
                ]
            }
        )
    )

    await run_extract_relationship(
        extractor=extractor,
        message_repo=msg_repo,
        relationship_repo=rel_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    new_row = next(r for r in rel_repo.rows if r.name == "李娟")
    assert new_row.via == "r-wife"


@pytest.mark.asyncio
async def test_run_extract_relationship_drops_via_when_unresolvable():
    rel_repo = FakeRelationshipRepo(user_id="u1")
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="我朋友老李", user_id="u1")
    extractor = RelationshipExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "relationships": [
                    {"name": "老李", "role": "朋友", "via_name": "陌生人"},
                ]
            }
        )
    )

    await run_extract_relationship(
        extractor=extractor,
        message_repo=msg_repo,
        relationship_repo=rel_repo,
        user_id="u1",
        conversation_id="c1",
        message_id="m1",
    )
    rel = next(r for r in rel_repo.rows if r.name == "老李")
    assert rel.via is None
