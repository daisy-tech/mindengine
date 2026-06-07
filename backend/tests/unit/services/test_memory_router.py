"""Memory router tests: hard rules, classifier, cache, policy, end-to-end."""

from __future__ import annotations

import pytest

from app.domain.llm import LLMRole
from app.domain.route import (
    ClassifyResult,
    Intent,
    IntentSource,
    MemoryDepth,
    Personality,
)
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.memory_router import (
    DEFAULT_POLICY,
    InMemoryIntentCache,
    LLMIntentClassifier,
    MemoryRouter,
    NullIntentCache,
    apply_hard_rules,
    apply_policy,
)
from app.services.memory_router.intent_cache import make_cache_key

# ─── hard rules ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("不对，我不是岳西人", Intent.CORRECTION),
        ("错了，应该是怀宁", Intent.CORRECTION),
        ("你还记得我老家在哪吗？", Intent.MEMORY_CHALLENGE),
        ("总结一下我", Intent.SELF_SUMMARY),
        ("我老婆最近不太开心", Intent.RELATIONSHIP_TOPIC),
        ("我好累", Intent.EMOTIONAL_SUPPORT),
    ],
)
def test_hard_rule_strong_signals(msg: str, expected: Intent) -> None:
    hit = apply_hard_rules(msg)
    assert hit is not None and hit.intent == expected


def test_hard_rule_misses_returns_none() -> None:
    assert apply_hard_rules("今天天气不错") is None
    assert apply_hard_rules("    ") is None
    assert apply_hard_rules("") is None


# ─── policy table ──────────────────────────────────────────────


def test_policy_covers_all_nine_intents() -> None:
    for intent in Intent:
        assert intent in DEFAULT_POLICY, f"missing policy for {intent}"


def test_correction_policy_minimal() -> None:
    entry = apply_policy(Intent.CORRECTION, personality=Personality.BALANCED)
    assert entry.memory_depth == MemoryDepth.MINIMAL
    assert entry.max_explicit_memories == 0


def test_emotional_support_is_sensitive() -> None:
    entry = apply_policy(Intent.EMOTIONAL_SUPPORT, personality=Personality.INTROVERT)
    assert entry.sensitive_mode is True


def test_unknown_intent_falls_back_to_casual() -> None:
    fake = apply_policy(Intent.CASUAL, personality=Personality.BALANCED)
    # Sanity: same as default lookup.
    assert fake.memory_depth == MemoryDepth.MINIMAL


# ─── classifier ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_happy_path() -> None:
    llm = MockLLMClient(role=LLMRole.INTENT).queue_json(
        {"intent": "self_summary", "confidence": 0.91, "reason": "user asks summary"}
    )
    clf = LLMIntentClassifier(llm=llm)
    result = await clf.classify("帮我看看我自己")
    assert result.intent == Intent.SELF_SUMMARY
    assert result.confidence > 0.9


@pytest.mark.asyncio
async def test_classifier_trims_history_to_window() -> None:
    llm = MockLLMClient(role=LLMRole.INTENT).queue_json(
        {"intent": "casual", "confidence": 0.6, "reason": "ok"}
    )
    clf = LLMIntentClassifier(llm=llm, history_window=2)
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    await clf.classify("hi", history=history)
    sent = llm.calls[0]["messages"]
    # Last 2 turns = 4 history msgs + the current user msg = 5 total.
    assert len(sent) == 5
    assert sent[0]["content"] == "u2"
    assert sent[-1]["content"] == "hi"


@pytest.mark.asyncio
async def test_classifier_falls_back_on_llm_error() -> None:
    llm = MockLLMClient(role=LLMRole.INTENT).queue(MockTurn(raises=LLMRateLimitError("429")))
    clf = LLMIntentClassifier(llm=llm)
    result = await clf.classify("hi")
    assert result.intent == Intent.CASUAL
    assert "fallback" in result.reason


# ─── intent cache ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_cache_get_set() -> None:
    cache = InMemoryIntentCache()
    val = ClassifyResult(intent=Intent.CASUAL, confidence=0.5, reason="x")
    await cache.set("u1", "k1", val)
    assert (await cache.get("u1", "k1")) == val
    assert (await cache.get("u2", "k1")) is None


@pytest.mark.asyncio
async def test_in_memory_cache_ttl_expires() -> None:
    cache = InMemoryIntentCache()
    val = ClassifyResult(intent=Intent.CASUAL, confidence=0.5, reason="x")
    await cache.set("u1", "k1", val, ttl_seconds=0)
    # ttl=0 = already expired
    assert (await cache.get("u1", "k1")) is None


@pytest.mark.asyncio
async def test_null_cache_never_stores() -> None:
    cache = NullIntentCache()
    val = ClassifyResult(intent=Intent.CASUAL, confidence=0.5, reason="x")
    await cache.set("u1", "k1", val)
    assert (await cache.get("u1", "k1")) is None


def test_cache_key_deterministic_and_short() -> None:
    h1 = make_cache_key("hello", [{"role": "user", "content": "hi"}])
    h2 = make_cache_key("hello", [{"role": "user", "content": "hi"}])
    h3 = make_cache_key("hello!", [{"role": "user", "content": "hi"}])
    assert h1 == h2 != h3
    assert len(h1) == 32


# ─── end-to-end router ────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_uses_hard_rule_when_present() -> None:
    cache = InMemoryIntentCache()
    llm = MockLLMClient(role=LLMRole.INTENT)
    router = MemoryRouter(classifier=LLMIntentClassifier(llm=llm), cache=cache)
    route = await router.route(user_id="u1", message="不对，是怀宁")
    assert route.intent == Intent.CORRECTION
    assert route.intent_source == IntentSource.HARD_RULE
    # Hard-rule decisions must NOT pollute the classifier cache.
    assert llm.calls == []
    assert cache.by_user == {} or all(not v for v in cache.by_user.values())


@pytest.mark.asyncio
async def test_router_consults_classifier_when_no_hard_rule() -> None:
    cache = InMemoryIntentCache()
    llm = MockLLMClient(role=LLMRole.INTENT).queue_json(
        {"intent": "self_summary", "confidence": 0.9, "reason": "ok"}
    )
    router = MemoryRouter(classifier=LLMIntentClassifier(llm=llm), cache=cache)
    route = await router.route(user_id="u1", message="帮我整理整理")
    assert route.intent == Intent.SELF_SUMMARY
    assert route.intent_source == IntentSource.SMALL_MODEL


@pytest.mark.asyncio
async def test_router_serves_from_cache_on_repeat() -> None:
    cache = InMemoryIntentCache()
    llm = MockLLMClient(role=LLMRole.INTENT).queue_json(
        {"intent": "preference_request", "confidence": 0.8, "reason": "ok"}
    )
    router = MemoryRouter(classifier=LLMIntentClassifier(llm=llm), cache=cache)
    msg = "我下班想吃什么？"
    r1 = await router.route(user_id="u1", message=msg)
    r2 = await router.route(user_id="u1", message=msg)
    assert r1.intent == r2.intent == Intent.PREFERENCE_REQUEST
    assert r1.intent_source == IntentSource.SMALL_MODEL
    assert r2.intent_source == IntentSource.CACHE
    assert len(llm.calls) == 1  # second call hit the cache


@pytest.mark.asyncio
async def test_router_classifier_fallback_on_llm_error() -> None:
    cache = InMemoryIntentCache()
    llm = MockLLMClient(role=LLMRole.INTENT).queue(MockTurn(raises=LLMRateLimitError("429")))
    router = MemoryRouter(classifier=LLMIntentClassifier(llm=llm), cache=cache)
    route = await router.route(user_id="u1", message="今天蓝天很美")
    assert route.intent == Intent.CASUAL
    assert route.intent_source == IntentSource.FALLBACK
    # Fallback decisions are NOT cached.
    assert all(not v for v in cache.by_user.values())


@pytest.mark.asyncio
async def test_router_populates_full_route_fields() -> None:
    cache = NullIntentCache()
    llm = MockLLMClient(role=LLMRole.INTENT)
    router = MemoryRouter(classifier=LLMIntentClassifier(llm=llm), cache=cache)
    route = await router.route(user_id="u1", message="我老婆怎么样了？")
    assert route.intent == Intent.RELATIONSHIP_TOPIC
    assert "relationships" in route.load_layers
    assert route.query == "我老婆怎么样了？"
    assert any("hard_rule" in r for r in route.reasons)
