"""Memory router — three-layer intent + policy resolver.

Per docs/rebuild/03-Subsystem-Memory-Router.md.

Public entry: ``MemoryRouter.route(user_id, message, history=...)`` returns a
:class:`app.domain.route.MemoryRoute` describing what to load + how to compose.
"""

from app.services.memory_router.classifier import (
    INTENT_CLASSIFIER_SYSTEM,
    LLMIntentClassifier,
)
from app.services.memory_router.hard_rules import (
    HardRule,
    HardRuleHit,
    apply_hard_rules,
    default_hard_rules,
)
from app.services.memory_router.intent_cache import (
    InMemoryIntentCache,
    NullIntentCache,
)
from app.services.memory_router.policy import (
    DEFAULT_POLICY,
    PolicyEntry,
    apply_policy,
)
from app.services.memory_router.router import MemoryRouter

__all__ = [
    "DEFAULT_POLICY",
    "HardRule",
    "HardRuleHit",
    "INTENT_CLASSIFIER_SYSTEM",
    "InMemoryIntentCache",
    "LLMIntentClassifier",
    "MemoryRouter",
    "NullIntentCache",
    "PolicyEntry",
    "apply_hard_rules",
    "apply_policy",
    "default_hard_rules",
]
