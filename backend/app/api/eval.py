"""HTTP endpoints for the evaluation lab.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §4 + §3.6 + §2.5.

Surface (all under ``/api/eval``):

- ``GET  /synthetic``                 — list available case files
- ``POST /synthetic/{name}/start``    — run a case set, return summary
- ``POST /seed-persona``              — seed the eval-only user (DEV)
- ``GET  /chat-audit/{conv_id}``      — review a conversation
- ``GET  /chat-audit-stored``         — list previously evaluated convs
- ``DELETE /chat-audit/{conv_id}``    — drop a stored review

The synthetic endpoint is an admin / dev surface — it requires the
caller's user_id to equal ``settings.eval_user_id`` (so we don't
accidentally run cases against a real persona) OR the dev override.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentUserId,
    MemoryReposDep,
    SessionDep,
    SettingsDep,
)
from app.domain.eval import EvalCase
from app.infra.repositories import MessageRepo
from app.services.eval_chat_review import (
    ReviewContext,
    TurnPack,
    list_stored_summaries,
    load_review,
    review_conversation,
    safe_id_segment,
    save_review,
)
from app.services.eval_chat_review.store import delete_review
from app.services.eval_synthetic import (
    DEFAULT_CASE_FILES,
    CaseLoadError,
    build_report,
    list_case_files,
    load_cases,
    seed_eval_persona,
)
from app.services.eval_synthetic.runner import (
    SyntheticBatch,
    SyntheticCaseRunner,
    SyntheticRunResult,
    SyntheticTurnOutcome,
    check_case,
)

router = APIRouter(prefix="/api/eval", tags=["eval"])


# ─── synthetic ────────────────────────────────────────────────────


class CaseFileInfo(BaseModel):
    name: str
    path: str
    case_count: int


class SyntheticReport(BaseModel):
    schema_: str = Field(default="synthetic_run_v1", alias="schema")
    run_id: str
    run_type: str
    started_at: str
    finished_at: str
    total: int
    passed: int
    pass_rate: float
    intent_confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    cases: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/synthetic", response_model=list[CaseFileInfo])
async def list_synthetic(settings: SettingsDep) -> list[CaseFileInfo]:
    files = list_case_files(settings.eval_synthetic_cases_dir)
    out: list[CaseFileInfo] = []
    for f in files:
        try:
            cases = load_cases(f)
        except CaseLoadError:
            continue
        out.append(
            CaseFileInfo(
                name=f.stem,
                path=str(f),
                case_count=len(cases),
            )
        )
    return out


def _ensure_eval_user(user_id: str, settings_eval_user_id: str) -> None:
    if user_id != settings_eval_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"synthetic endpoints require user_id == settings.eval_user_id "
                f"({settings_eval_user_id!r})"
            ),
        )


@router.post(
    "/synthetic/{name}/start",
    response_model=SyntheticReport,
)
async def start_synthetic(
    name: str,
    user_id: CurrentUserId,
    settings: SettingsDep,
    request: Request,
) -> SyntheticReport:
    """Run a named case set against an injected runner.

    The runner itself lives in ``app.state.synthetic_runner`` (set up in
    main.lifespan). Tests can override that attribute or use the
    ``DEPENDENCY_OVERRIDES`` machinery to swap in a fake.
    """
    _ensure_eval_user(user_id, settings.eval_user_id)
    safe_id_segment(name, label="case_set")

    fpath = Path(settings.eval_synthetic_cases_dir) / f"{name}.json"
    if not fpath.exists() and name in {f.replace(".json", "") for f in DEFAULT_CASE_FILES}:
        fpath = Path(settings.eval_synthetic_cases_dir) / f"{name}_cases.json"
    try:
        cases = load_cases(fpath)
    except CaseLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    runner = getattr(request.app.state, "synthetic_runner", None)
    if runner is None or not isinstance(runner, SyntheticCaseRunner):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "synthetic runner is not configured "
                "(set app.state.synthetic_runner in lifespan)"
            ),
        )

    batch = SyntheticBatch(started_at=datetime.now(UTC).timestamp())
    for case in cases:
        outcome = await _safe_run(runner, case)
        result = check_case(case, outcome)
        batch.items.append(SyntheticRunResult(case=case, outcome=outcome, result=result))
    batch.finished_at = datetime.now(UTC).timestamp()

    report = build_report(
        batch,
        run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
        run_type=name,
    )
    return SyntheticReport(**report)


async def _safe_run(
    runner: SyntheticCaseRunner, case: EvalCase
) -> SyntheticTurnOutcome:
    try:
        return await runner.run(case)
    except Exception as exc:  # pragma: no cover — defensive
        return SyntheticTurnOutcome(
            reply="", intent="", error=f"runner_error: {exc!r}"
        )


# ─── persona seed ──────────────────────────────────────────────────


class SeedPersonaResponse(BaseModel):
    user_id: str
    profile_set: bool
    events_inserted: int
    episodic_inserted: int
    relationships_upserted: int


@router.post("/seed-persona", response_model=SeedPersonaResponse)
async def seed_persona(
    repos: MemoryReposDep,
    user_id: CurrentUserId,
    settings: SettingsDep,
    session: SessionDep,
) -> SeedPersonaResponse:
    """Wipe & re-seed the eval persona's memory (DEV-mode gated)."""

    if not settings.allow_destructive_dev:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="seed-persona requires settings.allow_destructive_dev=true",
        )
    _ensure_eval_user(user_id, settings.eval_user_id)

    summary = await seed_eval_persona(
        user_id=user_id,
        profile_repo=repos.profile,
        event_repo=repos.event,
        episodic_repo=repos.episodic,
        relationship_repo=repos.relationship,
    )
    await session.commit()
    return SeedPersonaResponse(
        user_id=summary.user_id,
        profile_set=summary.profile_set,
        events_inserted=summary.events_inserted,
        episodic_inserted=summary.episodic_inserted,
        relationships_upserted=summary.relationships_upserted,
    )


# ─── chat audit (review) ──────────────────────────────────────────


class StoredReviewSummary(BaseModel):
    conversation_id: str
    evaluated_at: str
    turns_total: int
    evaluable_turns: int
    final_ok_rate: float
    counters: dict[str, int]


class StoredReviewIndex(BaseModel):
    items: list[StoredReviewSummary]


@router.get("/chat-audit-stored", response_model=StoredReviewIndex)
async def list_chat_audit_stored(
    user_id: CurrentUserId,
    settings: SettingsDep,
) -> StoredReviewIndex:
    items = list_stored_summaries(
        root=settings.eval_chat_reviews_dir,
        user_id=_safe_user(user_id),
    )
    return StoredReviewIndex(
        items=[
            StoredReviewSummary(
                conversation_id=it.conversation_id,
                evaluated_at=it.evaluated_at,
                turns_total=it.turns_total,
                evaluable_turns=it.evaluable_turns,
                final_ok_rate=it.final_ok_rate,
                counters=it.counters,
            )
            for it in items
        ]
    )


@router.get(
    "/chat-audit/{conversation_id}",
    response_model=dict[str, Any],
)
async def chat_audit(
    conversation_id: str,
    user_id: CurrentUserId,
    settings: SettingsDep,
    session: SessionDep,
    repos: MemoryReposDep,
    force: bool = False,
) -> dict[str, Any]:
    """Run (or load cached) review for one conversation.

    - ``force=false`` (default): if a review file already exists, return
      it from disk. Eval is otherwise expensive enough that we want
      callers to be explicit when re-running.
    - ``force=true``: re-evaluate and overwrite.
    """
    safe_user = _safe_user(user_id)
    safe_conv = _safe_conv(conversation_id)

    if not force:
        cached = load_review(
            root=settings.eval_chat_reviews_dir,
            user_id=safe_user,
            conversation_id=safe_conv,
        )
        if cached is not None:
            return cached

    # Build the turn packs from the conversation history.
    turns = await _build_turns(
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if not turns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No turns found (conversation empty or missing)",
        )

    ctx = await _build_review_context(repos)
    review = review_conversation(turns=turns, ctx=ctx)
    save_review(
        root=settings.eval_chat_reviews_dir,
        user_id=safe_user,
        conversation_id=safe_conv,
        review=review,
    )
    return review


@router.delete(
    "/chat-audit/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chat_audit(
    conversation_id: str,
    user_id: CurrentUserId,
    settings: SettingsDep,
) -> None:
    deleted = delete_review(
        root=settings.eval_chat_reviews_dir,
        user_id=_safe_user(user_id),
        conversation_id=_safe_conv(conversation_id),
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No stored review for that conversation",
        )


# ─── helpers ──────────────────────────────────────────────────────


def _safe_user(user_id: str) -> str:
    try:
        return safe_id_segment(user_id, label="user_id")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _safe_conv(conversation_id: str) -> str:
    try:
        return safe_id_segment(conversation_id, label="conv_id")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


async def _build_turns(
    *,
    session,
    user_id: str,
    conversation_id: str,
) -> list[TurnPack]:
    repo = MessageRepo(session=session, user_id=user_id)
    history = await repo.list_history(conversation_id, limit=500)
    turns: list[TurnPack] = []
    pending_user: Any = None
    pending_user_id: str | None = None
    pending_history: list[dict[str, str]] = []
    cursor_history: list[dict[str, str]] = []
    idx = 0
    for msg in history:
        if msg.role == "user":
            pending_user = msg.content
            pending_user_id = msg.id
            pending_history = list(cursor_history)
        elif msg.role == "assistant":
            user_text = pending_user or ""
            turns.append(
                TurnPack(
                    turn_id=msg.id,
                    index=idx,
                    user_message=user_text,
                    assistant_reply=msg.content,
                    error=getattr(msg, "error", None),
                    meta=msg.meta_json,
                    user_message_id=pending_user_id,
                    history_before=tuple(pending_history),
                )
            )
            idx += 1
            pending_user = None
            pending_user_id = None
            pending_history = list(cursor_history)
        cursor_history.append({"role": msg.role, "content": msg.content})
    return turns


async def _build_review_context(repos: MemoryReposDep) -> ReviewContext:
    known: set[str] = set()
    profile = await repos.profile.get()
    if profile is not None:
        if profile.basic.name:
            known.add(profile.basic.name)
        if profile.basic.location:
            known.add(profile.basic.location)
        for hobby in profile.interests or []:
            if hobby:
                known.add(hobby)
    rels = await repos.relationship.find_by_name("")
    # find_by_name with empty string returns []; iterate via list_for_intent
    # in the future. For now, walk recent relationship rows via a wide query.
    # The repo doesn't expose ``list_recent`` for relationships, so we
    # rely on the activated-pool fallback in L1 (it already inspects the
    # turn's activated list). Keep the known set conservative.
    _ = rels
    banned_rows = await repos.banned.list()
    banned = {b.entity for b in banned_rows if b.entity}
    return ReviewContext(known_entities=known, banned_entities=banned)


# Helpers exported for tests / parent factory.
__all__ = ["router"]


def fresh_run_id() -> str:
    return f"r_{uuid.uuid4().hex[:12]}"
