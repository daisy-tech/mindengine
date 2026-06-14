"""Worker runners — tested with fake repos + MockLLMClient."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.correction import BannedEntity
from app.domain.llm import LLMRole
from app.domain.memory import EpisodicHit, Event, Profile, Relationship
from app.infra.llm.mock_client import MockLLMClient
from app.services.memory_extract import (
    EpisodicExtractor,
    EventExtractor,
    ProfileExtractor,
    RelationshipExtractor,
)
from app.services.memory_extract.relationship_extractor import (
    RelationshipCandidate,
)
from app.workers.runners import (
    _topo_sort_relationship_candidates,
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


@pytest.mark.asyncio
async def test_run_extract_episodic_skips_in_batch_duplicate_string():
    """LLM 单轮内输出两条字面量相同的 fact —— 只能写入一条。

    回归:旧实现下游 episodic_repo.add 会被调两次,产生两条。
    """
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="奶黄又趴在我电脑边", user_id="u1")
    epi_repo = FakeEpisodicRepo(user_id="u1")
    banned = FakeBannedEntityRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "facts": [
                    "用户家里养了一只叫奶黄的猫",
                    "用户家里养了一只叫奶黄的猫",  # 字面量重复
                ]
            }
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
    # NOTE: EpisodicExtractor 自身已在 lower-cased set 里去过同字面量重复;
    # 所以传到 worker 这里只有 1 条 — 但 worker 这层依然要兜底,所以
    # 我们再压一次"模拟 extractor 没去重"的场景,见下一个 test。
    assert result["inserted"] == 1
    assert len(epi_repo.rows) == 1


@pytest.mark.asyncio
async def test_run_extract_episodic_skips_when_similar_in_db():
    """跨轮次重复:库里已存在同义旧记忆 —— ANN 命中后跳过。

    这是 18 条「奶黄」重复的真正修复:之前 worker 完全不查库。
    """
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="奶黄今天又来粘人", user_id="u1")
    epi_repo = FakeEpisodicRepo(
        user_id="u1",
        # FakeEpisodicRepo.find_similar 会返回 search_results[0],
        # 只要 score >= threshold 就视为重复。0.95 高于默认 0.90。
        search_results=[
            EpisodicHit(id="m_old", text="用户家里养了一只叫奶黄的猫", score=0.95)
        ],
    )
    banned = FakeBannedEntityRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {"facts": ["用户家里养了一只叫奶黄的猫"]}
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
    assert result["inserted"] == 0
    assert result["skipped_duplicate"] == 1
    assert epi_repo.rows == []  # 没插入


@pytest.mark.asyncio
async def test_run_extract_episodic_keeps_when_below_threshold():
    """语义不够近(score < threshold) —— 仍然插入,避免误杀。"""
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="我新养了一只小狗叫旺财", user_id="u1")
    epi_repo = FakeEpisodicRepo(
        user_id="u1",
        search_results=[
            EpisodicHit(id="m_old", text="用户家里养了一只叫奶黄的猫", score=0.55)
        ],
    )
    banned = FakeBannedEntityRepo(user_id="u1")
    extractor = EpisodicExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {"facts": ["用户养了一只叫旺财的狗"]}
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
    assert result["inserted"] == 1
    assert result["skipped_duplicate"] == 0
    assert len(epi_repo.rows) == 1


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


@pytest.mark.asyncio
async def test_run_extract_event_skips_in_batch_duplicate():
    """LLM 单轮输出两条同 (type, title) — 只能保留一条。"""
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m1",
              content="今天宅家学AI,顺便又复习了下AI", user_id="u1")
    event_repo = FakeEventRepo(user_id="u1")
    extractor = EventExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "events": [
                    {"type": "experience", "title": "宅家学习AI", "content": "今天"},
                    {"type": "experience", "title": "宅家学习AI", "content": "复习"},
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
    assert result["inserted"] == 1
    assert result["skipped_duplicate"] == 1
    assert len(event_repo.rows) == 1


@pytest.mark.asyncio
async def test_run_extract_event_skips_when_existing_in_window():
    """库里已经有相同 (type, title) 的近期事件 — 跳过。"""
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(msg_repo, conversation_id="c1", message_id="m2",
              content="今天又是宅家学AI的一天", user_id="u1")
    existing = Event(
        id="evt_old",
        user_id="u1",
        type="experience",
        title="宅家学习AI",
        content="昨天宅家学AI",
        occurred_at=None,
        source_message_id="m_old",
        created_at=datetime.now(UTC),
    )
    event_repo = FakeEventRepo(user_id="u1", rows=[existing])
    extractor = EventExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "events": [
                    {"type": "experience", "title": "宅家学习AI", "content": "今天"},
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
        message_id="m2",
    )
    assert result["inserted"] == 0
    assert result["skipped_duplicate"] == 1
    # 只有原本那一条
    assert len(event_repo.rows) == 1
    assert event_repo.rows[0].id == "evt_old"


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


# ─── _topo_sort_relationship_candidates 单测(纯函数,无 IO)─────────


def _cand(name: str, via: str | None = None) -> RelationshipCandidate:
    return RelationshipCandidate(name=name, role="朋友", via_name=via)


def test_topo_sort_empty_and_singleton():
    assert _topo_sort_relationship_candidates([]) == []
    one = [_cand("儿子")]
    assert _topo_sort_relationship_candidates(one) == one


def test_topo_sort_sons_friends_in_same_batch():
    """截图原始 case:儿子 + 两个朋友(via=儿子)同时出现,儿子必须排第一。"""
    got = [
        c.name
        for c in _topo_sort_relationship_candidates(
            [
                _cand("小孙孙", via="儿子"),
                _cand("儿子"),
                _cand("小魏魏", via="儿子"),
            ]
        )
    ]
    assert got[0] == "儿子", got
    assert set(got[1:]) == {"小孙孙", "小魏魏"}, got


def test_topo_sort_three_tier_chain():
    """三阶链:老婆 → 张姐(同事) → 小客户。中间人都在同批里。"""
    got = [
        c.name
        for c in _topo_sort_relationship_candidates(
            [
                _cand("小客户", via="张姐"),
                _cand("张姐", via="老婆"),
                _cand("老婆"),
            ]
        )
    ]
    assert got == ["老婆", "张姐", "小客户"], got


def test_topo_sort_external_via_treated_as_tier1():
    """via_name 不在本批 names 里 —— 当 tier1 处理,不当依赖。"""
    got = [
        c.name
        for c in _topo_sort_relationship_candidates(
            [_cand("小客户", via="张姐_不在批里")]
        )
    ]
    assert got == ["小客户"], got


def test_topo_sort_cycle_does_not_loop_forever():
    """A↔B 互 via —— 兜底为原顺序,不死循环。"""
    got = [
        c.name
        for c in _topo_sort_relationship_candidates(
            [_cand("A", via="B"), _cand("B", via="A")]
        )
    ]
    assert set(got) == {"A", "B"}, got


def test_topo_sort_neighbor_with_pet():
    """邻居老爷爷 + 他家的狗"可乐"(via=邻居老爷爷)。"""
    got = [
        c.name
        for c in _topo_sort_relationship_candidates(
            [_cand("可乐", via="邻居老爷爷"), _cand("邻居老爷爷")]
        )
    ]
    assert got == ["邻居老爷爷", "可乐"], got


@pytest.mark.asyncio
async def test_run_extract_relationship_resolves_via_within_same_batch():
    """同一批 candidates 里,via 中间人会被先 upsert,孩子节点的 via 能挂上。

    这是最关键的回归 —— 没拓扑排序之前,先处理 "小孙孙" 时数据库里
    还没有 "儿子",via_id 拿到 None,二阶链路被静默丢失。
    """
    rel_repo = FakeRelationshipRepo(user_id="u1")
    msg_repo = FakeMessageRepo(user_id="u1")
    _seed_msg(
        msg_repo,
        conversation_id="c1",
        message_id="m1",
        content="我儿子和他朋友小孙孙周末一起玩",
        user_id="u1",
    )
    extractor = RelationshipExtractor(
        llm=MockLLMClient(role=LLMRole.EXTRACT).queue_json(
            {
                "relationships": [
                    # 故意把孩子节点放在父节点之前,模拟 LLM 输出顺序
                    {"name": "小孙孙", "role": "朋友", "via_name": "儿子"},
                    {"name": "儿子", "role": "儿子"},
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
    son = next(r for r in rel_repo.rows if r.name == "儿子")
    friend = next(r for r in rel_repo.rows if r.name == "小孙孙")
    assert son.via is None, "儿子是一阶,via 应为 None"
    assert friend.via == son.id, (
        f"小孙孙应当 via 儿子的 id;实际拿到 {friend.via!r},预期 {son.id!r}"
    )
