"""Layer 3 of the router: intent → routing policy table.

Per docs/rebuild/03-Subsystem-Memory-Router.md §5.

The policy entry for each intent describes:
- which memory layers to load
- how aggressive memory_depth should be
- max_explicit_memories (how many of the activated items may be quoted in
  the system prompt vs. kept as background knowledge)
- whether to enter "sensitive_mode" (for emotional support — gentler tone,
  no probing questions)
- event policy (none / summary / background pain points)

Lesson 8.2: every policy field must be explicit; do NOT rely on defaults
silently propagating between intents.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.route import (
    EventPolicy,
    Intent,
    LoadLayer,
    MemoryDepth,
    Personality,
)


@dataclass(frozen=True)
class PolicyEntry:
    memory_depth: MemoryDepth
    load_layers: tuple[LoadLayer, ...]
    max_explicit_memories: int
    sensitive_mode: bool = False
    event_policy: EventPolicy = EventPolicy.NONE


# Hand-tuned per doc 03 §5 reference table. Tweak via eval, never on a hunch.
DEFAULT_POLICY: dict[Intent, PolicyEntry] = {
    Intent.CASUAL: PolicyEntry(
        memory_depth=MemoryDepth.MINIMAL,
        load_layers=("profile_basic",),
        max_explicit_memories=0,
    ),
    Intent.SELF_SUMMARY: PolicyEntry(
        memory_depth=MemoryDepth.WIDE,
        load_layers=("profile", "events", "relationships", "episodic"),
        max_explicit_memories=4,
        event_policy=EventPolicy.SUMMARY,
    ),
    Intent.MEMORY_CHALLENGE: PolicyEntry(
        memory_depth=MemoryDepth.FOCUSED,
        load_layers=("profile_basic", "episodic"),
        max_explicit_memories=3,
    ),
    Intent.RELATIONSHIP_TOPIC: PolicyEntry(
        memory_depth=MemoryDepth.FOCUSED,
        load_layers=("profile_basic", "relationships", "episodic"),
        max_explicit_memories=3,
    ),
    Intent.EMOTIONAL_SUPPORT: PolicyEntry(
        memory_depth=MemoryDepth.SAFE_FOCUSED,
        load_layers=("profile_basic", "episodic"),
        max_explicit_memories=1,
        sensitive_mode=True,
        event_policy=EventPolicy.BACKGROUND_PAIN_POINTS,
    ),
    Intent.PLAN_FOLLOWUP: PolicyEntry(
        memory_depth=MemoryDepth.FOCUSED,
        load_layers=("profile_basic", "events"),
        max_explicit_memories=2,
        event_policy=EventPolicy.SUMMARY,
    ),
    Intent.PREFERENCE_REQUEST: PolicyEntry(
        memory_depth=MemoryDepth.FOCUSED,
        load_layers=("profile_basic", "profile", "episodic"),
        max_explicit_memories=2,
    ),
    Intent.CORRECTION: PolicyEntry(
        # Low memory load on correction turn — we're going to mutate state,
        # not generate creative output.
        memory_depth=MemoryDepth.MINIMAL,
        load_layers=("profile_basic",),
        max_explicit_memories=0,
    ),
    Intent.KNOWLEDGE_TASK: PolicyEntry(
        memory_depth=MemoryDepth.MINIMAL,
        load_layers=("profile_basic",),
        max_explicit_memories=0,
    ),
}


def apply_policy(
    intent: Intent,
    *,
    personality: Personality,
    policy: dict[Intent, PolicyEntry] | None = None,
) -> PolicyEntry:
    """Resolve a route entry. Personality may shrink/expand counts in future
    revisions; for M2 we keep policy + personality decoupled.
    """
    table = policy or DEFAULT_POLICY
    if intent not in table:
        # Defensive: never raise on unknown intent (could be from a future
        # eval set or a fallback). Fall back to CASUAL behavior.
        intent = Intent.CASUAL
    base = table[intent]
    _ = personality  # explicit hook for future tuning
    return base
