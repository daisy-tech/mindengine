"""Redis-backed adapters.

Per docs/rebuild/02-TDD.md §3.2.
"""

from app.infra.cache.intent_cache_redis import RedisIntentCache

__all__ = ["RedisIntentCache"]
