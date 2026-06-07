"""TaskDispatcher implementations (in-memory + Celery facade).

In M2 we ship the in-memory variant; M3 wires the Celery facade.
"""

from app.infra.tasks.in_memory import InMemoryDispatcher, RecordedTask

__all__ = ["InMemoryDispatcher", "RecordedTask"]
