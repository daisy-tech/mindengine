"""Intent cache (D4 / docs/rebuild/03-Subsystem-Memory-Router.md §8).

Two implementations:
- :class:`InMemoryIntentCache` — TTL'd dict, used by tests and as a safe
  fallback when Redis is unavailable.
- (Redis-backed impl lives in :mod:`app.infra.cache`.)

Cache key strategy: hash of (user_id, last 2 turns + new message) so a
mid-conversation rephrasing reuses the prior classification.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.route import ClassifyResult


def make_cache_key(
    message: str,
    history: Sequence[dict[str, str]] | None = None,
    *,
    history_window: int = 2,
) -> str:
    """Deterministic, length-bounded cache key.

    The key is independent of `user_id` because per-user namespacing is
    handled by the cache adapter (Redis prefix or InMemory user_map).
    """
    parts: list[str] = []
    pairs = max(0, history_window) * 2
    for m in (history or [])[-pairs:]:
        parts.append(f"{m.get('role', '?')}:{(m.get('content') or '').strip()}")
    parts.append(f"user:{(message or '').strip()}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


@dataclass
class _Entry:
    value: ClassifyResult
    expires_at: float


@dataclass
class InMemoryIntentCache:
    """Process-local TTL cache. Coroutine-safe via a single lock."""

    by_user: dict[str, dict[str, _Entry]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self, user_id: str, key: str) -> ClassifyResult | None:
        async with self._lock:
            user_map = self.by_user.get(user_id)
            if not user_map:
                return None
            entry = user_map.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                user_map.pop(key, None)
                return None
            return entry.value

    async def set(
        self,
        user_id: str,
        key: str,
        value: ClassifyResult,
        *,
        ttl_seconds: int = 300,
    ) -> None:
        async with self._lock:
            user_map = self.by_user.setdefault(user_id, {})
            user_map[key] = _Entry(
                value=value,
                expires_at=time.monotonic() + ttl_seconds,
            )

    async def clear(self) -> None:
        async with self._lock:
            self.by_user.clear()


@dataclass
class NullIntentCache:
    """No-op cache — useful when intent-cache is disabled via settings."""

    async def get(self, user_id: str, key: str) -> ClassifyResult | None:
        _ = (user_id, key)
        return None

    async def set(
        self,
        user_id: str,
        key: str,
        value: ClassifyResult,
        *,
        ttl_seconds: int = 300,
    ) -> None:
        _ = (user_id, key, value, ttl_seconds)
