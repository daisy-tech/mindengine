"""Celery application skeleton.

Real tasks (`extract_memory_task`, `extract_profile_task`,
`extract_event_task`, `correction_cleanup_task`) are added in M3/M4.

Conventions:
- broker / backend = Redis (settings.redis_url)
- task_routes for `correction:*` may be configured in M4 to run on a
  dedicated worker (lower noise neighborhood)
- per docs/rebuild/11-Roadmap.md M3: with Postgres MVCC we no longer need
  redis_lock per user — keep this in mind when adding tasks.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings


def create_celery() -> Celery:
    settings = get_settings()
    celery = Celery(
        "mindengine",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[],
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_default_queue="mindengine.default",
        task_routes={
            "correction.*": {"queue": "mindengine.correction"},
        },
    )
    return celery


celery_app = create_celery()
