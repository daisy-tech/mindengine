"""Shared types for L0/L1/reviewer.

Kept in a sibling module to avoid the ``__init__`` ↔ rules import cycle
(both ``l0_rules`` and ``l1_rules`` need TurnPack / RuleResult).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RuleStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SUSPICIOUS = "suspicious"
    SKIP = "skip"


class RuleSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class RuleResult:
    id: str
    status: RuleStatus
    severity: RuleSeverity
    detail: str = ""
    attribution: str | None = None  # AttributionCode value, optional


@dataclass(frozen=True)
class TurnPack:
    """One assistant turn + the user msg that prompted it.

    The eval pipeline consumes a ``Sequence[TurnPack]`` (chronological).
    Use ``from_audit_messages`` to build packs from a conversation audit
    export — see ``reviewer.from_audit_export``.
    """

    turn_id: str  # assistant message id
    index: int
    user_message: str
    assistant_reply: str
    error: str | None
    meta: dict[str, Any] | None
    user_message_id: str | None = None
    history_before: Sequence[dict[str, str]] = field(default_factory=tuple)


# Convenience shortcuts so call-sites don't have to import StrEnums.
PASS = RuleStatus.PASS
FAIL = RuleStatus.FAIL
SUSPICIOUS = RuleStatus.SUSPICIOUS
SKIP = RuleStatus.SKIP

HIGH = RuleSeverity.HIGH
MEDIUM = RuleSeverity.MEDIUM
LOW = RuleSeverity.LOW
