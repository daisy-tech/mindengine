"""Tests for the four LLM-driven extractors using MockLLMClient."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.memory_extract import (
    EpisodicExtractor,
    EventExtractor,
    ProfileExtractor,
    RelationshipExtractor,
)


def _mock(role: LLMRole = LLMRole.EXTRACT) -> MockLLMClient:
    return MockLLMClient(role=role, model="mock-extract")


# ───────────────────────────────────────── profile ──


@pytest.mark.asyncio
async def test_profile_extractor_happy_path():
    llm = _mock().queue_json(
        {
            "basic": {"name": "张三", "location": "上海"},
            "interests": ["跑步"],
            "occupation": {"title": "工程师"},
            "extra": {"hobby_status": "active"},
        }
    )
    extractor = ProfileExtractor(llm=llm)
    partial = await extractor.extract("我叫张三，在上海，喜欢跑步")
    assert partial["basic"]["name"] == "张三"
    assert partial["interests"] == ["跑步"]
    assert partial["occupation"] == {"title": "工程师"}
    assert partial["extra"] == {"hobby_status": "active"}


@pytest.mark.asyncio
async def test_profile_extractor_drops_empty_dicts():
    llm = _mock().queue_json({"basic": {}, "interests": []})
    extractor = ProfileExtractor(llm=llm)
    partial = await extractor.extract("hello")
    assert partial == {}


@pytest.mark.asyncio
async def test_profile_extractor_swallows_llm_errors_returns_empty():
    llm = _mock().queue(MockTurn(raises=LLMRateLimitError("429")))
    extractor = ProfileExtractor(llm=llm)
    partial = await extractor.extract("hello")
    assert partial == {}


@pytest.mark.asyncio
async def test_profile_extractor_window_clips_history():
    llm = _mock().queue_json({"interests": ["游泳"]})
    extractor = ProfileExtractor(llm=llm, history_window=1)
    history = [
        {"role": "user", "content": "old1"},
        {"role": "assistant", "content": "old1-resp"},
        {"role": "user", "content": "old2"},
        {"role": "assistant", "content": "old2-resp"},
    ]
    await extractor.extract("我喜欢游泳", history=history)
    sent = llm.calls[-1]["messages"]
    assert len(sent) == 3  # last 2 history msgs + current user
    assert sent[0]["content"] == "old2"


# ───────────────────────────────────────── event ──


@pytest.mark.asyncio
async def test_event_extractor_returns_typed_candidates():
    llm = _mock().queue_json(
        {
            "events": [
                {
                    "type": "experience",
                    "title": "周末和儿子吃饭",
                    "content": "周日和儿子去吃了大餐，他点了披萨",
                },
                {
                    "type": "plan",
                    "title": "下周面试",
                    "content": "下周一面试 X 公司",
                },
            ]
        }
    )
    extractor = EventExtractor(llm=llm)
    cands = await extractor.extract("周末和儿子吃饭，下周一面试 X 公司")
    assert [c.type for c in cands] == ["experience", "plan"]
    assert all(len(c.title) <= 80 for c in cands)


@pytest.mark.asyncio
async def test_event_extractor_caps_to_max_per_turn():
    payload = {
        "events": [
            {"type": "experience", "title": f"e{i}", "content": "x"}
            for i in range(20)
        ]
    }
    llm = _mock().queue_json(payload)
    extractor = EventExtractor(llm=llm, max_events_per_turn=3)
    cands = await extractor.extract("hello")
    assert len(cands) == 3


@pytest.mark.asyncio
async def test_event_extractor_returns_empty_on_invalid_payload():
    # Mock validates with Pydantic — invalid payload raises LLMInvalidJSONError
    # which the extractor swallows.
    llm = _mock().queue_json({"events": [{"type": "bad_type", "title": "x", "content": "y"}]})
    extractor = EventExtractor(llm=llm)
    cands = await extractor.extract("hello")
    assert cands == []


# ───────────────────────────────────────── episodic ──


@pytest.mark.asyncio
async def test_episodic_extractor_strips_dups_and_short_strings():
    llm = _mock().queue_json(
        {
            "facts": [
                "用户的儿子喜欢台球",
                "用户每周和儿子打台球",
                "用户的儿子喜欢台球",  # dup
                "短",                  # too short
                "   ",                  # blank
            ]
        }
    )
    extractor = EpisodicExtractor(llm=llm)
    facts = await extractor.extract("我儿子喜欢台球，我们每周一起打")
    assert facts == ["用户的儿子喜欢台球", "用户每周和儿子打台球"]


@pytest.mark.asyncio
async def test_episodic_extractor_truncates_overly_long_facts():
    long_fact = "A" * 500
    llm = _mock().queue_json({"facts": [long_fact]})
    extractor = EpisodicExtractor(llm=llm, max_fact_length=80)
    facts = await extractor.extract("hello")
    assert len(facts) == 1
    assert len(facts[0]) == 80


@pytest.mark.asyncio
async def test_episodic_extractor_caps_fact_count():
    payload = {"facts": [f"事实片段编号 {i}：用户做了某事" for i in range(20)]}
    llm = _mock().queue_json(payload)
    extractor = EpisodicExtractor(llm=llm, max_facts_per_turn=4)
    facts = await extractor.extract("hello")
    assert len(facts) == 4


# ───────────────────────────────────────── relationship ──


@pytest.mark.asyncio
async def test_relationship_extractor_dedups_same_person():
    llm = _mock().queue_json(
        {
            "relationships": [
                {"name": "妻子张三", "role": "妻子", "attributes": {"职业": "医生"}},
                {"name": "妻子张三", "role": "妻子"},
                {"name": "儿子小宝", "role": "儿子"},
            ]
        }
    )
    extractor = RelationshipExtractor(llm=llm)
    rels = await extractor.extract("我老婆是医生，我儿子小宝今年5岁")
    names = [r.name for r in rels]
    assert names == ["妻子张三", "儿子小宝"]


@pytest.mark.asyncio
async def test_relationship_extractor_rejects_self_loop_via():
    llm = _mock().queue_json(
        {
            "relationships": [
                {"name": "张三", "role": "妻子", "via_name": "张三"},
            ]
        }
    )
    extractor = RelationshipExtractor(llm=llm)
    rels = await extractor.extract("hello")
    # Pydantic validator on RelationshipCandidate rejects → extractor swallows.
    assert rels == []


@pytest.mark.asyncio
async def test_relationship_extractor_caps_per_turn():
    payload = {
        "relationships": [
            {"name": f"朋友{i}", "role": "朋友"} for i in range(10)
        ]
    }
    llm = _mock().queue_json(payload)
    extractor = RelationshipExtractor(llm=llm, max_per_turn=2)
    rels = await extractor.extract("hi")
    assert len(rels) == 2
