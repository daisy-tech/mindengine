"""L0 structural self-check rules (no LLM).

Per docs/rebuild/07-Subsystem-Eval-Lab.md §3.2.

These check that the persisted PromptMeta is well-formed enough that
later rules can rely on its shape. A high-severity L0 fail short-
circuits the L1 evaluation for that turn (see reviewer).

Lesson 3.5: don't string-match section titles — index by SectionKey.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.domain.prompt import SectionKey
from app.domain.route import Intent, Personality
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    MEDIUM,
    PASS,
    SKIP,
    RuleResult,
    TurnPack,
)

L0Rule = Callable[[TurnPack], RuleResult]


def _has_meta(turn: TurnPack) -> dict[str, Any] | None:
    return turn.meta if isinstance(turn.meta, dict) else None


# ─── high severity ────────────────────────────────────────────────


def rule_prompt_meta_exists(turn: TurnPack) -> RuleResult:
    if _has_meta(turn) is not None:
        return RuleResult(id="prompt_meta.exists", status=PASS, severity=HIGH)
    return RuleResult(
        id="prompt_meta.exists",
        status=FAIL,
        severity=HIGH,
        detail="meta_json missing on assistant message",
    )


def rule_assistant_reply_nonempty(turn: TurnPack) -> RuleResult:
    txt = (turn.assistant_reply or "").strip()
    if txt:
        return RuleResult(id="assistant_reply_nonempty", status=PASS, severity=HIGH)
    return RuleResult(
        id="assistant_reply_nonempty",
        status=FAIL,
        severity=HIGH,
        detail="assistant reply was empty",
    )


def rule_system_excerpt_nonempty(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="prompt_meta.system_nonempty",
            status=SKIP,
            severity=HIGH,
            detail="no meta",
        )
    if (meta.get("system_excerpt") or "").strip():
        return RuleResult(
            id="prompt_meta.system_nonempty", status=PASS, severity=HIGH
        )
    return RuleResult(
        id="prompt_meta.system_nonempty",
        status=FAIL,
        severity=HIGH,
        detail="system_excerpt empty",
    )


def rule_intent_in_valid_set(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="route.intent_in_valid_set", status=SKIP, severity=HIGH, detail="no meta"
        )
    intent = ((meta.get("route") or {}).get("intent")) or ""
    valid = {i.value for i in Intent}
    if intent in valid:
        return RuleResult(
            id="route.intent_in_valid_set", status=PASS, severity=HIGH
        )
    return RuleResult(
        id="route.intent_in_valid_set",
        status=FAIL,
        severity=HIGH,
        detail=f"intent {intent!r} not in {sorted(valid)}",
    )


def rule_personality_in_valid_set(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="route.personality_in_valid_set",
            status=SKIP,
            severity=HIGH,
            detail="no meta",
        )
    pers = ((meta.get("route") or {}).get("personality")) or ""
    valid = {p.value for p in Personality}
    if pers in valid:
        return RuleResult(
            id="route.personality_in_valid_set", status=PASS, severity=HIGH
        )
    return RuleResult(
        id="route.personality_in_valid_set",
        status=FAIL,
        severity=HIGH,
        detail=f"personality {pers!r} not in {sorted(valid)}",
    )


# ─── medium severity ──────────────────────────────────────────────


def _section_keys(meta: dict[str, Any]) -> set[str]:
    return set(meta.get("section_keys") or [])


def rule_explicit_section(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="explicit_in_system_section",
            status=SKIP,
            severity=MEDIUM,
            detail="no meta",
        )
    keys = _section_keys(meta)
    if SectionKey.EXPLICIT_MEMORIES.value in keys:
        return RuleResult(
            id="explicit_in_system_section", status=PASS, severity=MEDIUM
        )
    # Composer may legitimately drop the explicit section when nothing was
    # activated. Skip rather than fail in that case (lesson 6.2).
    activated = meta.get("activated") or []
    if not activated:
        return RuleResult(
            id="explicit_in_system_section",
            status=SKIP,
            severity=MEDIUM,
            detail="no activated items",
        )
    return RuleResult(
        id="explicit_in_system_section",
        status=FAIL,
        severity=MEDIUM,
        detail="activated present but no EXPLICIT_MEMORIES section",
    )


def rule_background_section(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="background_in_system_section",
            status=SKIP,
            severity=MEDIUM,
            detail="no meta",
        )
    keys = _section_keys(meta)
    snap = meta.get("snapshot_stats") or {}
    has_background_data = bool(
        (snap.get("episodic_total") or 0)
        + (snap.get("event_total") or 0)
        + (snap.get("relationship_total") or 0)
    )
    if SectionKey.BACKGROUND_MEMORIES.value in keys:
        return RuleResult(
            id="background_in_system_section", status=PASS, severity=MEDIUM
        )
    if not has_background_data:
        return RuleResult(
            id="background_in_system_section",
            status=SKIP,
            severity=MEDIUM,
            detail="empty pools",
        )
    return RuleResult(
        id="background_in_system_section",
        status=FAIL,
        severity=MEDIUM,
        detail="pools have items but no BACKGROUND_MEMORIES section",
    )


def rule_activated_consistent_with_pools(turn: TurnPack) -> RuleResult:
    meta = _has_meta(turn)
    if meta is None:
        return RuleResult(
            id="activated_consistent_with_pools",
            status=SKIP,
            severity=MEDIUM,
            detail="no meta",
        )
    activated = meta.get("activated") or []
    snap = meta.get("snapshot_stats") or {}
    pool_total = sum(
        int(snap.get(k) or 0)
        for k in (
            "profile_total",
            "episodic_total",
            "event_total",
            "relationship_total",
        )
    )
    if pool_total == 0:
        # When pools are empty, ``activated`` should also be empty.
        if not activated:
            return RuleResult(
                id="activated_consistent_with_pools",
                status=PASS,
                severity=MEDIUM,
            )
        return RuleResult(
            id="activated_consistent_with_pools",
            status=FAIL,
            severity=MEDIUM,
            detail=f"activated={len(activated)} but pools empty",
        )
    # Otherwise activated should never *exceed* the visible pool size.
    if len(activated) <= pool_total:
        return RuleResult(
            id="activated_consistent_with_pools", status=PASS, severity=MEDIUM
        )
    return RuleResult(
        id="activated_consistent_with_pools",
        status=FAIL,
        severity=MEDIUM,
        detail=f"activated={len(activated)} > pool_total={pool_total}",
    )


L0_RULES: list[L0Rule] = [
    rule_prompt_meta_exists,
    rule_assistant_reply_nonempty,
    rule_system_excerpt_nonempty,
    rule_intent_in_valid_set,
    rule_personality_in_valid_set,
    rule_explicit_section,
    rule_background_section,
    rule_activated_consistent_with_pools,
]


def run_l0_rules(turn: TurnPack) -> list[RuleResult]:
    """Apply every L0 rule. Returns results in declaration order."""
    out: list[RuleResult] = []
    for rule in L0_RULES:
        try:
            out.append(rule(turn))
        except Exception as exc:  # pragma: no cover — defensive
            out.append(
                RuleResult(
                    id=getattr(rule, "__name__", "l0_unknown"),
                    status=SKIP,
                    severity=MEDIUM,
                    detail=f"rule_error: {exc!r}",
                )
            )
    return out


def has_high_fail(results: Iterable[RuleResult]) -> bool:
    return any(
        r.status == FAIL and r.severity == HIGH for r in results
    )


def has_medium_fail(results: Iterable[RuleResult]) -> bool:
    return any(
        r.status == FAIL and r.severity == MEDIUM for r in results
    )
