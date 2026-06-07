"""Layer 1 of the router: regex-driven hard rules.

Per docs/rebuild/03-Subsystem-Memory-Router.md §3.

Each rule is a (regex, intent, reason) triple. The first match wins;
unmatched messages fall through to the small-model classifier.

Intentionally conservative — only fire on signals strong enough that we
don't want the LLM to second-guess them (e.g. explicit correction, "你记得"
challenges). Anything ambiguous goes to layer 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.route import Intent


@dataclass(frozen=True)
class HardRule:
    pattern: re.Pattern[str]
    intent: Intent
    reason: str
    confidence: float = 1.0


@dataclass(frozen=True)
class HardRuleHit:
    intent: Intent
    confidence: float
    reason: str


def default_hard_rules() -> list[HardRule]:
    """Returns the production hard-rule list (frozen).

    Lesson 8.1: keep this list small and reviewable; if you find yourself
    adding more than 12 rules, the right answer is usually to widen the
    classifier prompt instead.
    """
    return [
        # ─── corrections (highest priority — must NEVER fall to small model)
        HardRule(
            pattern=re.compile(r"^(不对|错了|不是|你记错|你搞错|说错了)"),
            intent=Intent.CORRECTION,
            reason="hard_rule:correction.opening",
        ),
        HardRule(
            pattern=re.compile(r"(我没有|我不是|我从来没|哪里有|没说过).{0,8}$"),
            intent=Intent.CORRECTION,
            reason="hard_rule:correction.denial",
        ),
        # ─── memory-challenge (asking what the assistant remembers)
        HardRule(
            pattern=re.compile(r"你(还)?(记得|记不记得|有没有印象).{0,40}\??"),
            intent=Intent.MEMORY_CHALLENGE,
            reason="hard_rule:memory_challenge",
        ),
        HardRule(
            pattern=re.compile(r"(上次|之前|前几天|那天).{0,30}(说|讲|提)"),
            intent=Intent.MEMORY_CHALLENGE,
            reason="hard_rule:memory_challenge.recall",
            confidence=0.85,
        ),
        # ─── self-summary requests
        HardRule(
            pattern=re.compile(r"(总结一下|帮我看看|聊聊我自己|我是个怎样)"),
            intent=Intent.SELF_SUMMARY,
            reason="hard_rule:self_summary",
        ),
        # ─── relationship topic
        HardRule(
            pattern=re.compile(r"(我(老婆|老公|妻子|丈夫|爸爸|妈妈|爸|妈|儿子|女儿))"),
            intent=Intent.RELATIONSHIP_TOPIC,
            reason="hard_rule:relationship_topic",
            confidence=0.9,
        ),
        # ─── emotional support (very conservative — easy to over-classify)
        HardRule(
            pattern=re.compile(r"(我?(好(累|烦|难受|郁闷)|心情(不好|糟))).{0,20}$"),
            intent=Intent.EMOTIONAL_SUPPORT,
            reason="hard_rule:emotional_support",
            confidence=0.85,
        ),
    ]


def apply_hard_rules(
    message: str, rules: list[HardRule] | None = None
) -> HardRuleHit | None:
    """Returns the first matching rule's verdict, or None.

    The pattern list is small enough that a linear scan is the right
    answer; switching to compiled scanners only matters above ~50 rules.
    """
    msg = (message or "").strip()
    if not msg:
        return None
    for rule in rules or default_hard_rules():
        if rule.pattern.search(msg):
            return HardRuleHit(
                intent=rule.intent,
                confidence=rule.confidence,
                reason=rule.reason,
            )
    return None
