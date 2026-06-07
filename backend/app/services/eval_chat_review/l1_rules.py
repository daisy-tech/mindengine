"""L1 heuristic rules (no LLM by default).

Per docs/rebuild/07-Subsystem-Eval-Lab.md §3.3 + §3.3a.

Each rule signature:
    rule(turn, prev, ctx) -> RuleResult

``ctx`` carries cross-turn data: known structured entities, the previous
turn, and any cached banned-entities list. Rules may return:
- pass    — everything looks right (or the rule doesn't apply: SKIP)
- fail    — high-confidence regression (red)
- suspicious — borderline (orange; eligible for §3.3a JUDGE re-review)
- skip    — rule N/A for this intent / data shape

The implementation deliberately favors *high recall* on the "user-
visible failure" axis at the cost of some false positives — those are
expected to be the inputs to the optional LLM judge in §3.3a.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.domain.route import Intent, Personality
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    LOW,
    MEDIUM,
    PASS,
    SKIP,
    SUSPICIOUS,
    RuleResult,
    TurnPack,
)
from app.services.prompt_composer.personality import contract_for

# ─── ReviewContext ────────────────────────────────────────────────


@dataclass
class ReviewContext:
    """Cross-turn / cross-conversation inputs for the L1 rules.

    The default factory yields an empty context; callers (typically
    ``reviewer.review_conversation``) populate ``known_entities`` and
    ``banned_entities`` from the user's structured stores before the
    fan-out begins.
    """

    known_entities: set[str] = field(default_factory=set)
    banned_entities: set[str] = field(default_factory=set)
    intent_whitelist_for_off_topic: set[str] = field(
        default_factory=lambda: {
            Intent.MEMORY_CHALLENGE.value,
            Intent.PREFERENCE_REQUEST.value,
            Intent.SELF_SUMMARY.value,
            Intent.KNOWLEDGE_TASK.value,
        }
    )


# ─── helpers ──────────────────────────────────────────────────────


_GENERIC_PATTERNS = (
    "嗯",
    "好的",
    "明白",
    "听起来",
    "我能感受到",
    "我会注意",
    "下次不会",
    "已记录",
    "好滴",
    "ok",
    "我明白了",
)
_ERROR_MARKERS = (
    "[出错",
    "系统错误",
    "AI 服务异常",
    "出错了",
    "请求失败",
)


def _meta(turn: TurnPack) -> dict[str, Any]:
    return turn.meta if isinstance(turn.meta, dict) else {}


def _intent(turn: TurnPack) -> str | None:
    return ((_meta(turn).get("route") or {}).get("intent")) or None


def _personality(turn: TurnPack) -> str | None:
    return ((_meta(turn).get("route") or {}).get("personality")) or None


def _activated(turn: TurnPack) -> list[dict[str, Any]]:
    items = _meta(turn).get("activated") or []
    return [a for a in items if isinstance(a, dict)]


def _activated_text_blob(turn: TurnPack) -> str:
    return "\n".join(str(a.get("excerpt") or "") for a in _activated(turn))


def _snapshot_total(turn: TurnPack) -> int:
    snap = _meta(turn).get("snapshot_stats") or {}
    return sum(
        int(snap.get(k) or 0)
        for k in (
            "profile_total",
            "episodic_total",
            "event_total",
            "relationship_total",
        )
    )


def _char_2grams(text: str) -> set[str]:
    txt = re.sub(r"\s+", "", text or "")
    return {txt[i : i + 2] for i in range(len(txt) - 1)} if len(txt) >= 2 else set()


def _ends_with_question(text: str) -> bool:
    txt = (text or "").rstrip()
    return txt.endswith("？") or txt.endswith("?")


def _user_keywords(text: str) -> list[str]:
    """Cheap CN/EN keyword extractor: 2-grams of CJK runs + ASCII words."""
    out: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for i in range(len(run) - 1):
            out.append(run[i : i + 2])
    out.extend(w.lower() for w in re.findall(r"[A-Za-z]{2,}", text or ""))
    return out


L1Rule = Callable[[TurnPack, TurnPack | None, ReviewContext], RuleResult]


# ─── rules ────────────────────────────────────────────────────────


def rule_error_reply(turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext) -> RuleResult:
    _ = prev, ctx
    txt = turn.assistant_reply or ""
    if turn.error or any(m in txt for m in _ERROR_MARKERS):
        return RuleResult(
            id="error_reply",
            status=FAIL,
            severity=HIGH,
            detail=f"error marker: {turn.error or 'in_text'}",
        )
    return RuleResult(id="error_reply", status=PASS, severity=HIGH)


def rule_recall_for_challenge(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    if _intent(turn) != Intent.MEMORY_CHALLENGE.value:
        return RuleResult(id="recall_for_challenge", status=SKIP, severity=HIGH)
    keywords = _user_keywords(turn.user_message)
    if not keywords:
        return RuleResult(id="recall_for_challenge", status=SKIP, severity=HIGH)
    pool_blob = _activated_text_blob(turn).lower()
    if not pool_blob:
        return RuleResult(
            id="recall_for_challenge",
            status=FAIL,
            severity=HIGH,
            detail="activated pool empty",
        )
    if any(k.lower() in pool_blob for k in keywords):
        return RuleResult(id="recall_for_challenge", status=PASS, severity=HIGH)
    return RuleResult(
        id="recall_for_challenge",
        status=FAIL,
        severity=HIGH,
        detail="user keywords not present in activated pool",
    )


def rule_recall_for_self_summary(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    if _intent(turn) != Intent.SELF_SUMMARY.value:
        return RuleResult(id="recall_for_self_summary", status=SKIP, severity=HIGH)
    if _activated(turn):
        return RuleResult(id="recall_for_self_summary", status=PASS, severity=HIGH)
    if _snapshot_total(turn) == 0:
        return RuleResult(
            id="recall_for_self_summary",
            status=FAIL,
            severity=HIGH,
            detail="self_summary with empty pools",
        )
    return RuleResult(
        id="recall_for_self_summary",
        status=FAIL,
        severity=HIGH,
        detail="self_summary but nothing activated despite non-empty pools",
    )


def rule_data_vs_activation_gap(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    pool_total = _snapshot_total(turn)
    activated = _activated(turn)
    if pool_total == 0:
        return RuleResult(
            id="data_vs_activation_gap", status=SKIP, severity=MEDIUM
        )
    if activated:
        return RuleResult(
            id="data_vs_activation_gap", status=PASS, severity=MEDIUM
        )
    intent = _intent(turn)
    # Casual / knowledge_task may legitimately not need any activation.
    if intent in {Intent.KNOWLEDGE_TASK.value, Intent.CASUAL.value}:
        return RuleResult(
            id="data_vs_activation_gap", status=SKIP, severity=MEDIUM
        )
    return RuleResult(
        id="data_vs_activation_gap",
        status=SUSPICIOUS,
        severity=MEDIUM,
        detail=f"pool={pool_total} but activated=0",
    )


def rule_generic_reply(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    txt = (turn.assistant_reply or "").strip()
    if not txt:
        return RuleResult(id="generic_reply", status=SKIP, severity=MEDIUM)
    char_count = len(txt)
    if char_count <= 6:
        return RuleResult(
            id="generic_reply",
            status=FAIL,
            severity=MEDIUM,
            detail=f"reply too short ({char_count} chars)",
        )
    hits = sum(1 for p in _GENERIC_PATTERNS if p in txt)
    if hits >= 2 and char_count <= 24:
        return RuleResult(
            id="generic_reply",
            status=SUSPICIOUS,
            severity=MEDIUM,
            detail=f"generic patterns hit={hits}",
        )
    return RuleResult(id="generic_reply", status=PASS, severity=MEDIUM)


def rule_reply_off_topic(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev
    intent = _intent(turn)
    if intent in ctx.intent_whitelist_for_off_topic:
        return RuleResult(id="reply_off_topic", status=SKIP, severity=MEDIUM)
    user = _char_2grams(turn.user_message)
    reply = _char_2grams(turn.assistant_reply)
    if not user or not reply:
        return RuleResult(id="reply_off_topic", status=SKIP, severity=MEDIUM)
    overlap = user & reply
    if overlap:
        return RuleResult(id="reply_off_topic", status=PASS, severity=MEDIUM)
    if _ends_with_question(turn.assistant_reply):
        return RuleResult(
            id="reply_off_topic", status=PASS, severity=MEDIUM,
            detail="no overlap but reply asks a follow-up question",
        )
    return RuleResult(
        id="reply_off_topic",
        status=SUSPICIOUS,
        severity=MEDIUM,
        detail="0% 2-gram overlap and no follow-up question",
    )


def rule_correction_persisted(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    if prev is None or _intent(prev) != Intent.CORRECTION.value:
        return RuleResult(id="correction_persisted", status=SKIP, severity=HIGH)
    bans = ctx.banned_entities
    if not bans:
        return RuleResult(id="correction_persisted", status=SKIP, severity=HIGH)
    reply = (turn.assistant_reply or "").lower()
    leaked = [b for b in bans if b and b.lower() in reply]
    if leaked:
        return RuleResult(
            id="correction_persisted",
            status=FAIL,
            severity=HIGH,
            detail=f"banned re-emerged: {leaked!r}",
        )
    return RuleResult(id="correction_persisted", status=PASS, severity=HIGH)


def rule_correction_no_concrete_ack(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    if _intent(turn) != Intent.CORRECTION.value:
        return RuleResult(
            id="correction_no_concrete_ack", status=SKIP, severity=HIGH
        )
    # Expect the reply to echo at least one chunk of the user's correction.
    user_grams = _char_2grams(turn.user_message)
    reply_grams = _char_2grams(turn.assistant_reply)
    if not user_grams or not reply_grams:
        return RuleResult(
            id="correction_no_concrete_ack", status=SKIP, severity=HIGH
        )
    if user_grams & reply_grams:
        return RuleResult(
            id="correction_no_concrete_ack", status=PASS, severity=HIGH
        )
    return RuleResult(
        id="correction_no_concrete_ack",
        status=FAIL,
        severity=HIGH,
        detail="correction reply shares no 2-gram with user message",
    )


def rule_fabrication_under_challenge(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev
    if _intent(turn) != Intent.MEMORY_CHALLENGE.value:
        return RuleResult(
            id="fabrication_under_challenge", status=SKIP, severity=HIGH
        )
    activated_blob = _activated_text_blob(turn).lower()
    reply = (turn.assistant_reply or "")
    # Find capitalized / Chinese name candidates in the reply that look
    # like specific entities (≥2 CJK chars or ≥3 ASCII chars).
    candidates = re.findall(
        r"[\u4e00-\u9fff]{2,8}|[A-Z][A-Za-z]{2,}", reply
    )
    if not candidates:
        return RuleResult(
            id="fabrication_under_challenge", status=PASS, severity=HIGH
        )
    known_lower = {k.lower() for k in ctx.known_entities if k}
    suspect: list[str] = []
    for c in candidates:
        cl = c.lower()
        # Match if ANY known entity is a substring of the CJK run (handles
        # "李娜" being inside "李娜在产品岗"), or if the candidate appears
        # somewhere in the activated pool's excerpts.
        if any(k and k in cl for k in known_lower):
            continue
        if cl in activated_blob:
            continue
        if any(k and k in cl for k in (e.lower() for e in re.findall(r"[\u4e00-\u9fff]{2,8}", activated_blob))):
            continue
        suspect.append(c)
    if not suspect:
        return RuleResult(
            id="fabrication_under_challenge", status=PASS, severity=HIGH
        )
    return RuleResult(
        id="fabrication_under_challenge",
        status=SUSPICIOUS,
        severity=HIGH,
        detail=f"off-pool entities: {suspect[:5]!r}",
    )


def rule_followup_overflow(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    txt = turn.assistant_reply or ""
    q_count = txt.count("？") + txt.count("?")
    if q_count <= 2:
        return RuleResult(id="followup_overflow", status=PASS, severity=LOW)
    return RuleResult(
        id="followup_overflow",
        status=SUSPICIOUS,
        severity=LOW,
        detail=f"{q_count} questions in one reply",
    )


def rule_personality_signature(
    turn: TurnPack, prev: TurnPack | None, ctx: ReviewContext
) -> RuleResult:
    _ = prev, ctx
    intent = _intent(turn)
    # Knowledge tasks legitimately bend the persona shape.
    if intent == Intent.KNOWLEDGE_TASK.value:
        return RuleResult(
            id="personality_signature", status=SKIP, severity=MEDIUM
        )
    pers_value = _personality(turn)
    if not pers_value:
        return RuleResult(
            id="personality_signature", status=SKIP, severity=MEDIUM
        )
    try:
        pers = Personality(pers_value)
    except ValueError:
        return RuleResult(
            id="personality_signature", status=SKIP, severity=MEDIUM
        )
    contract = contract_for(pers)
    txt = turn.assistant_reply or ""
    raw_chars = len(txt)
    issues: list[str] = []
    if raw_chars > contract.max_chars:
        issues.append(f"len {raw_chars}>{contract.max_chars}")
    if not contract.allow_question and _ends_with_question(txt):
        issues.append("introvert ended with ?")
    if issues:
        return RuleResult(
            id="personality_signature",
            status=FAIL,
            severity=MEDIUM,
            detail=", ".join(issues),
        )
    return RuleResult(id="personality_signature", status=PASS, severity=MEDIUM)


L1_RULES: list[L1Rule] = [
    rule_error_reply,
    rule_recall_for_challenge,
    rule_recall_for_self_summary,
    rule_data_vs_activation_gap,
    rule_generic_reply,
    rule_reply_off_topic,
    rule_correction_persisted,
    rule_correction_no_concrete_ack,
    rule_fabrication_under_challenge,
    rule_followup_overflow,
    rule_personality_signature,
]


def run_l1_rules(
    turn: TurnPack,
    prev: TurnPack | None,
    ctx: ReviewContext | None = None,
) -> list[RuleResult]:
    """Apply every L1 rule in order. Defensive: a single rule failing
    raises a SKIP rather than aborting the whole turn."""
    cx = ctx or ReviewContext()
    out: list[RuleResult] = []
    for rule in L1_RULES:
        try:
            out.append(rule(turn, prev, cx))
        except Exception as exc:  # pragma: no cover — defensive
            out.append(
                RuleResult(
                    id=getattr(rule, "__name__", "l1_unknown"),
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


def has_suspicious(results: Iterable[RuleResult]) -> bool:
    return any(r.status == SUSPICIOUS for r in results)
