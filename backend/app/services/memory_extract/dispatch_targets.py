"""Per-intent dispatch targets (which extractors to fire after a turn).

The router output (Intent) decides which subset of {profile, event,
episodic, relationship} to extract from this turn's user message.

Decisions (keep alongside doc 03/06 — change here, document there):
- CASUAL: any layer is fair game (the bulk of memory accrues here).
- EMOTIONAL_SUPPORT, PLAN_FOLLOWUP: user is *telling* something; pull
  events + episodic.
- RELATIONSHIP_TOPIC: relationships first, episodic for context.
- PREFERENCE_REQUEST: tighten to profile (interests/extra).
- KNOWLEDGE_TASK / SELF_SUMMARY / MEMORY_CHALLENGE: user is *asking*;
  nothing to extract.
- CORRECTION: extract is muted — correction_cleanup runs in M4 instead
  (doc 06 §2: extract and correction must be mutually exclusive).
"""

from __future__ import annotations

from typing import Literal

from app.domain.route import Intent

ExtractLayer = Literal["episodic", "profile", "event", "relationship"]

ALL_LAYERS: frozenset[ExtractLayer] = frozenset(
    {"episodic", "profile", "event", "relationship"}
)

EXTRACT_TARGETS_BY_INTENT: dict[Intent, frozenset[ExtractLayer]] = {
    Intent.CASUAL: ALL_LAYERS,
    Intent.EMOTIONAL_SUPPORT: frozenset({"episodic", "event"}),
    Intent.PLAN_FOLLOWUP: frozenset({"event", "episodic"}),
    Intent.RELATIONSHIP_TOPIC: frozenset({"relationship", "episodic"}),
    Intent.PREFERENCE_REQUEST: frozenset({"profile", "episodic"}),
    Intent.KNOWLEDGE_TASK: frozenset(),
    Intent.SELF_SUMMARY: frozenset(),
    Intent.MEMORY_CHALLENGE: frozenset(),
    # Correction goes through correction_cleanup (M4); don't double-extract.
    Intent.CORRECTION: frozenset(),
}


def targets_for(intent: Intent) -> frozenset[ExtractLayer]:
    return EXTRACT_TARGETS_BY_INTENT.get(intent, frozenset())
