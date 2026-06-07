"""MemoryRouter — orchestrates hard rules + cache + classifier + policy.

Per docs/rebuild/03-Subsystem-Memory-Router.md §5.

Flow per call:
    1. Hard rules: if any fire, use that intent. Cache MISS-and-skip
       (don't pollute the cache with hard-rule decisions; they're free
       to recompute on the next turn).
    2. Cache lookup keyed by (user_id, last-2-turns + message). Hit ⇒
       use cached ClassifyResult.
    3. Small-model classifier. On any LLM error → CASUAL fallback.
    4. Policy lookup: turn the resolved intent into a full MemoryRoute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.route import (
    ClassifyResult,
    Intent,
    IntentSource,
    MemoryRoute,
    Personality,
)
from app.services.memory_router.classifier import LLMIntentClassifier
from app.services.memory_router.hard_rules import HardRule, apply_hard_rules
from app.services.memory_router.intent_cache import (
    InMemoryIntentCache,
    NullIntentCache,
    make_cache_key,
)
from app.services.memory_router.policy import DEFAULT_POLICY, PolicyEntry
from app.services.protocols import IntentCache


@dataclass
class MemoryRouter:
    classifier: LLMIntentClassifier
    cache: IntentCache
    hard_rules: list[HardRule] | None = None
    policy: dict[Intent, PolicyEntry] | None = None
    cache_ttl_seconds: int = 300

    async def route(
        self,
        *,
        user_id: str,
        message: str,
        history: Sequence[dict[str, str]] | None = None,
        personality: Personality = Personality.BALANCED,
    ) -> MemoryRoute:
        reasons: list[str] = []

        # 1. Hard rules.
        hit = apply_hard_rules(message, self.hard_rules)
        if hit is not None:
            reasons.append(hit.reason)
            return self._compose(
                intent=hit.intent,
                source=IntentSource.HARD_RULE,
                confidence=hit.confidence,
                personality=personality,
                message=message,
                reasons=reasons,
            )

        # 2. Cache lookup (D4).
        key = make_cache_key(message, history)
        cached = await self.cache.get(user_id, key)
        if cached is not None:
            reasons.append(f"cache:hit:{cached.intent.value}")
            return self._compose(
                intent=cached.intent,
                source=IntentSource.CACHE,
                confidence=cached.confidence,
                personality=personality,
                message=message,
                reasons=reasons,
            )

        # 3. Small-model classifier.
        result: ClassifyResult = await self.classifier.classify(message, history)
        reasons.append(f"classifier:{result.intent.value}:{result.confidence:.2f}")

        if result.reason and "fallback" in result.reason:
            source = IntentSource.FALLBACK
        else:
            source = IntentSource.SMALL_MODEL
            # Only cache real model outputs, not fallbacks.
            await self.cache.set(user_id, key, result, ttl_seconds=self.cache_ttl_seconds)

        return self._compose(
            intent=result.intent,
            source=source,
            confidence=result.confidence,
            personality=personality,
            message=message,
            reasons=reasons,
        )

    # ─── internals ────────────────────────────────────────────────

    def _compose(
        self,
        *,
        intent: Intent,
        source: IntentSource,
        confidence: float,
        personality: Personality,
        message: str,
        reasons: list[str],
    ) -> MemoryRoute:
        table = self.policy or DEFAULT_POLICY
        entry = table.get(intent) or table[Intent.CASUAL]
        return MemoryRoute(
            intent=intent,
            intent_source=source,
            intent_confidence=confidence,
            personality=personality,
            memory_depth=entry.memory_depth,
            load_layers=list(entry.load_layers),
            sensitive_mode=entry.sensitive_mode,
            max_explicit_memories=entry.max_explicit_memories,
            event_policy=entry.event_policy,
            query=message,
            reasons=reasons,
        )


def build_default_router(classifier: LLMIntentClassifier) -> MemoryRouter:
    """Convenience factory used by api.deps when Redis is absent."""
    return MemoryRouter(
        classifier=classifier,
        cache=InMemoryIntentCache(),
    )


def build_disabled_router(classifier: LLMIntentClassifier) -> MemoryRouter:
    """Used in tests / dev mode where caching is undesirable."""
    return MemoryRouter(classifier=classifier, cache=NullIntentCache())
