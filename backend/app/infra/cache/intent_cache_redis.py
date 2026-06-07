"""Redis-backed intent cache implementation.

Production option for the IntentCache Protocol; in-memory and null impls
live in ``app.services.memory_router.intent_cache``.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.domain.route import ClassifyResult


@dataclass
class RedisIntentCache:
    redis: Redis
    namespace: str = "mindengine:intent"

    def _key(self, user_id: str, key: str) -> str:
        return f"{self.namespace}:{user_id}:{key}"

    async def get(self, user_id: str, key: str) -> ClassifyResult | None:
        raw = await self.redis.get(self._key(user_id, key))
        if raw is None:
            return None
        try:
            payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            return ClassifyResult.model_validate_json(payload)
        except Exception:  # noqa: BLE001 — defensive, never crash on cached garbage
            return None

    async def set(
        self,
        user_id: str,
        key: str,
        value: ClassifyResult,
        *,
        ttl_seconds: int = 300,
    ) -> None:
        await self.redis.set(
            self._key(user_id, key),
            value.model_dump_json(),
            ex=ttl_seconds,
        )
