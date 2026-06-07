"""Celery worker entrypoint.

Worker boot order (see ``services.bootstrap_worker_services``):
  settings → DB engine/sessionmaker → LLM router → Embedder → IntentCache.

Tasks are imported eagerly so Celery autodiscovers them.
"""

from app.workers.celery_app import celery_app
from app.workers.tasks_after_chat import (
    after_chat,
    extract_episodic,
    extract_event,
    extract_profile,
    extract_relationship,
)
from app.workers.tasks_correction import correction_cleanup

__all__ = [
    "after_chat",
    "celery_app",
    "correction_cleanup",
    "extract_episodic",
    "extract_event",
    "extract_profile",
    "extract_relationship",
]
