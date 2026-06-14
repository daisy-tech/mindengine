"""Async runners executed by Celery tasks.

Splitting the async logic out of the Celery wrappers + the IO boundary
gives us:
- Direct unit tests against ``await runner(...)`` with fake repos.
- A single place per task to take/release ``AsyncSession`` and commit.

Each runner returns a small dict summarizing what happened — the Celery
task surfaces it back as the AsyncResult payload (handy in tests + logs).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.memory import Event, Relationship
from app.services.correction.applier import ApplyOutcome, CorrectionApplier
from app.services.correction.banned_extractor import BannedExtractor
from app.services.correction.extractor import CorrectionTargetExtractor
from app.services.correction.judge import CorrectionJudge, JudgementOutcome
from app.services.correction.searcher import CorrectionCandidateSearcher
from app.services.memory_extract.episodic_extractor import EpisodicExtractor
from app.services.memory_extract.event_extractor import EventExtractor
from app.services.memory_extract.profile_extractor import ProfileExtractor
from app.services.memory_extract.profile_merger import ProfileMerger
from app.services.memory_extract.relationship_extractor import (
    RelationshipCandidate,
    RelationshipExtractor,
)
from app.services.protocols import (
    BannedEntityRepository,
    EpisodicRepository,
    EventRepository,
    MessageRepository,
    ProfileRepository,
    RelationshipRepository,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Helpers shared by every runner
# ─────────────────────────────────────────────────────────────────


async def load_turn_context(
    *,
    message_repo: MessageRepository,
    conversation_id: str,
    message_id: str,
) -> tuple[Any, list[dict[str, str]]] | None:
    """Find the user message + a small window of priors.

    Returns ``(target_message, history_for_llm)`` or ``None`` when the
    target is missing / not authored by the user (worker should silently
    skip; the chat path may have been cancelled before persistence).
    """
    history = await message_repo.list_history(conversation_id, limit=12)
    target = next(
        (m for m in history if m.id == message_id and m.role == "user"),
        None,
    )
    if target is None:
        return None
    prior: list[dict[str, str]] = []
    for m in history:
        if m.id == message_id:
            break
        prior.append({"role": m.role, "content": m.content})
    return target, prior


def _hits_banned(text: str, banned: Sequence[str]) -> bool:
    if not banned:
        return False
    needle = text.lower()
    return any(b and b.lower() in needle for b in banned)


# ─────────────────────────────────────────────────────────────────
# Episodic
# ─────────────────────────────────────────────────────────────────


async def run_extract_episodic(
    *,
    extractor: EpisodicExtractor,
    message_repo: MessageRepository,
    episodic_repo: EpisodicRepository,
    banned_repo: BannedEntityRepository,
    conversation_id: str,
    message_id: str,
    dedup_threshold: float = 0.90,
) -> dict[str, Any]:
    """Extract + persist episodic facts.

    去重链路(从外到内,任一命中即跳过):
    1. 用户的 banned-entities 命中:跳过(老路径)
    2. 同批内已经写过(``inserted_in_this_run``):跳过 —— LLM 偶尔
       一次输出里就重复同一句
    3. 库里已有 cosine sim ≥ ``dedup_threshold`` 的 active 记忆:跳过
       (这是 18 条「奶黄」重复的根因 — v2.0.2.7 之前完全没做)
    """
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    facts = await extractor.extract(target.content, history=prior)
    if not facts:
        return {"status": "ok", "inserted": 0}

    banned = [b.entity for b in await banned_repo.list()]
    inserted: list[str] = []
    skipped_banned = 0
    skipped_duplicate = 0
    inserted_texts_lower: set[str] = set()
    for fact in facts:
        if _hits_banned(fact, banned):
            skipped_banned += 1
            continue
        # 同一批内的字符串重复 —— 比 ANN 更便宜,先拦
        key = fact.strip().lower()
        if key in inserted_texts_lower:
            skipped_duplicate += 1
            continue
        # 跨轮次的语义重复 —— 走 pgvector ANN
        try:
            existing = await episodic_repo.find_similar(
                fact, threshold=dedup_threshold
            )
        except Exception:
            # ANN 暂时不可用不应当让整个抽取失败 —— 退回到不去重的旧行为
            existing = None
        if existing is not None:
            skipped_duplicate += 1
            continue

        mem_id = await episodic_repo.add(
            fact,
            metadata={
                "id": str(uuid.uuid4()),
                "source_message_id": message_id,
                "source": "extract_memory",
            },
        )
        if mem_id:
            inserted.append(mem_id)
            inserted_texts_lower.add(key)

    return {
        "status": "ok",
        "inserted": len(inserted),
        "skipped_banned": skipped_banned,
        "skipped_duplicate": skipped_duplicate,
        "ids": inserted,
    }


# ─────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────


async def run_extract_profile(
    *,
    extractor: ProfileExtractor,
    message_repo: MessageRepository,
    profile_repo: ProfileRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    partial = await extractor.extract(target.content, history=prior)
    if not partial:
        return {"status": "ok", "fields": 0}

    merger = ProfileMerger()
    current = await profile_repo.get()
    seeded = dict(partial)
    if current is None:
        seeded.setdefault("user_id", user_id)
    merged = merger.merge(current=current, partial=seeded)
    await profile_repo.upsert(merged)

    return {"status": "ok", "fields": _count_partial_fields(partial)}


def _count_partial_fields(partial: dict[str, Any]) -> int:
    n = 0
    for value in partial.values():
        if isinstance(value, dict):
            n += sum(1 for v in value.values() if v not in (None, "", []))
        elif isinstance(value, list):
            n += len(value)
        elif value:
            n += 1
    return n


# ─────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────


async def run_extract_event(
    *,
    extractor: EventExtractor,
    message_repo: MessageRepository,
    event_repo: EventRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
    dedup_window_days: int = 30,
) -> dict[str, Any]:
    """Extract + persist event candidates.

    去重链路(从轻到重):
    1. 同批内 (type, normalized title) 重复 → 跳过 (LLM 单轮重复)
    2. ``event_repo.find_similar_recent`` 命中 → 跳过 (跨轮次重复)
       字符串归一(剥首尾标点 + 大小写),并允许子串包含(≥4 字)。
       事件没有 embedding 字段(doc 08 §3),只能字符串去重;同义改写
       (e.g. "心情烦躁" vs "感到烦躁")抓不到 —— 那要靠 LLM prompt
       自身不要复述,以及未来给 events 表加 embedding 列。
    """
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    candidates = await extractor.extract(target.content, history=prior)
    if not candidates:
        return {"status": "ok", "inserted": 0}

    inserted: list[str] = []
    skipped_duplicate = 0
    now = datetime.now(UTC)
    seen_in_batch: set[tuple[str, str]] = set()
    for cand in candidates:
        title = cand.title[:200]
        norm = (title or "").strip().lower()
        sig = (cand.type, norm)
        if sig in seen_in_batch:
            skipped_duplicate += 1
            continue
        try:
            existing = await event_repo.find_similar_recent(
                type_=cand.type,
                title=title,
                within_days=dedup_window_days,
            )
        except Exception:
            existing = None
        if existing is not None:
            skipped_duplicate += 1
            continue

        event = Event(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=cand.type,
            title=title,
            content=cand.content,
            occurred_at=cand.occurred_at,
            source_message_id=message_id,
            created_at=now,
        )
        await event_repo.insert(event)
        inserted.append(event.id)
        seen_in_batch.add(sig)

    return {
        "status": "ok",
        "inserted": len(inserted),
        "skipped_duplicate": skipped_duplicate,
        "ids": inserted,
    }


# ─────────────────────────────────────────────────────────────────
# Relationship
# ─────────────────────────────────────────────────────────────────


async def run_extract_relationship(
    *,
    extractor: RelationshipExtractor,
    message_repo: MessageRepository,
    relationship_repo: RelationshipRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    ctx = await load_turn_context(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if ctx is None:
        return {"status": "skipped", "reason": "message_not_found"}
    target, prior = ctx

    candidates = await extractor.extract(target.content, history=prior)
    if not candidates:
        return {"status": "ok", "inserted": 0}

    # 同一批 candidates 里，"小孙孙 via_name=儿子" 和 "儿子" 经常出现在一起 ——
    # 如果朴素地按 LLM 给的顺序 upsert，先处理 "小孙孙" 时 find_by_name("儿子")
    # 还查不到（"儿子" 自己也才正在被插入），二阶链路就会静默丢失 (lesson 4.3)。
    #
    # 拓扑排序解决：把 via_name 在本批 names 集合里的 candidate 后置，让中间人
    # 一定先入库。环或互相 via 的极端情况按 LLM 顺序兜底。
    upserted: list[str] = []
    name_to_rel_id: dict[str, str] = {}  # 同批新插入的 name → rel_id 缓存

    for cand in _topo_sort_relationship_candidates(candidates):
        existing = await relationship_repo.find_by_name(cand.name)
        via_id: str | None = None
        if cand.via_name:
            # 优先用本批已插入的中间人（最常见路径），再 fallback 到库里查
            via_id = name_to_rel_id.get(cand.via_name)
            if via_id is None:
                via_rows = await relationship_repo.find_by_name(cand.via_name)
                via_id = via_rows[0].id if via_rows else None

        if existing:
            target_rel = existing[0]
            merged_attrs = dict(target_rel.attributes)
            merged_attrs.update(cand.attributes)
            rel = Relationship(
                schema_version=target_rel.schema_version,
                id=target_rel.id,
                user_id=user_id,
                name=target_rel.name,
                role=target_rel.role or cand.role,
                attributes=merged_attrs,
                # 已有的 via 优先（不 reset），新 via_id 只在历史为空时填上
                via=target_rel.via or via_id,
                status="active",
                created_at=target_rel.created_at,
            )
        else:
            rel_id = str(uuid.uuid4())
            if via_id == rel_id:
                via_id = None
            rel = Relationship(
                id=rel_id,
                user_id=user_id,
                name=cand.name,
                role=cand.role,
                attributes=dict(cand.attributes),
                via=via_id,
            )
        await relationship_repo.upsert(rel)
        upserted.append(rel.id)
        # 注册到批内缓存，让后续 candidate 用 via_name 找得到
        name_to_rel_id[rel.name] = rel.id

    return {"status": "ok", "upserted": len(upserted), "ids": upserted}


def _topo_sort_relationship_candidates(
    candidates: list[RelationshipCandidate],
) -> list[RelationshipCandidate]:
    """Reorder candidates so middlemen come before their dependents.

    例：candidates=[小孙孙(via=儿子), 儿子, 小魏魏(via=儿子)]
        →               [儿子, 小孙孙(via=儿子), 小魏魏(via=儿子)]

    实现是 Kahn 风格的简单拓扑：本批里 ``via_name`` 没出现在
    ``names`` 集合里的 candidate 视作"无内部依赖"先发；剩下的按依赖
    解开顺序追加。环或自指（被 schema validator 拦了，但稳妥起见）
    按原顺序兜底，避免无限循环。
    """
    if len(candidates) <= 1:
        return list(candidates)
    names = {c.name.strip() for c in candidates}

    no_internal_dep: list[RelationshipCandidate] = []
    has_internal_dep: list[RelationshipCandidate] = []
    for c in candidates:
        via = (c.via_name or "").strip()
        if via and via in names and via != c.name.strip():
            has_internal_dep.append(c)
        else:
            no_internal_dep.append(c)

    ordered: list[RelationshipCandidate] = list(no_internal_dep)
    inserted_names = {c.name.strip() for c in ordered}
    pending = list(has_internal_dep)
    # 最多迭代 len(pending) 轮 —— 每轮至少解开一条依赖；超过这个上限
    # 说明出现环，按 LLM 原顺序追加避免死循环。
    for _ in range(len(pending) + 1):
        if not pending:
            break
        progressed = False
        still: list[RelationshipCandidate] = []
        for c in pending:
            via = (c.via_name or "").strip()
            if via in inserted_names:
                ordered.append(c)
                inserted_names.add(c.name.strip())
                progressed = True
            else:
                still.append(c)
        pending = still
        if not progressed:
            break
    ordered.extend(pending)  # 兜底：仍然没解开的按原相对顺序追加
    return ordered


# ─────────────────────────────────────────────────────────────────
# Correction cleanup
# ─────────────────────────────────────────────────────────────────


async def _load_correction_pair(
    *,
    message_repo: MessageRepository,
    conversation_id: str,
    message_id: str,
) -> tuple[Any, Any | None] | None:
    """Locate the user's correction message + the AI message immediately
    preceding it.

    Returns ``(user_msg, ai_msg | None)`` or ``None`` if the user message
    is missing (worker should silently skip).
    """
    history = await message_repo.list_history(conversation_id, limit=12)
    # ``list_history`` is chronological (oldest → newest). Find the user
    # correction message and the most-recent assistant turn before it.
    user_msg = None
    ai_msg = None
    for m in history:
        if m.id == message_id and m.role == "user":
            user_msg = m
            break
        if m.role == "assistant":
            ai_msg = m
    if user_msg is None:
        return None
    return user_msg, ai_msg


async def run_correction_cleanup(
    *,
    target_extractor: CorrectionTargetExtractor,
    searcher: CorrectionCandidateSearcher,
    judge: CorrectionJudge,
    banned_extractor: BannedExtractor,
    applier: CorrectionApplier,
    message_repo: MessageRepository,
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    """End-to-end correction cleanup for one user-correction message.

    Returns a small dict suitable for Celery result backends + tests.
    """
    pair = await _load_correction_pair(
        message_repo=message_repo,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    if pair is None:
        return {"status": "skipped", "reason": "message_not_found"}
    user_msg, ai_msg = pair

    targets = await target_extractor.extract(
        user_correction=user_msg.content,
        ai_previous_reply=(ai_msg.content if ai_msg else ""),
    )
    if not targets:
        return {"status": "ok", "targets": 0, "applied": 0}

    bundle = await searcher.search(targets)
    outcomes: list[JudgementOutcome] = []
    for cand in bundle.items:
        outcomes.append(
            await judge.judge(
                user_correction=user_msg.content,
                ai_previous_reply=(ai_msg.content if ai_msg else ""),
                candidate=cand,
            )
        )

    banned = await banned_extractor.extract(
        user_correction=user_msg.content,
        targets=targets,
    )

    apply_out: ApplyOutcome = await applier.apply(
        outcomes=outcomes,
        banned=banned,
        user_id=user_id,
        conversation_id=conversation_id,
        correction_message_id=message_id,
        user_correction_text=user_msg.content,
    )

    return {
        "status": "ok",
        "targets": len(targets),
        "candidates": len(bundle.items),
        "applied": apply_out.episodic_soft_deleted
        + apply_out.event_soft_deleted
        + apply_out.profile_fields_patched,
        "audit_only": apply_out.audit_only,
        "banned_inserted": apply_out.banned_inserted,
        "deprecations_inserted": apply_out.deprecations_inserted,
        "notes": apply_out.notes,
    }


__all__ = [
    "ApplyOutcome",
    "load_turn_context",
    "run_correction_cleanup",
    "run_extract_episodic",
    "run_extract_event",
    "run_extract_profile",
    "run_extract_relationship",
]
