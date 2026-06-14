"""One-shot historical-dedup CLI for episodic + event memories.

Why this exists
---------------

v2.0.2.8 added on-line dedup at the **worker** layer so that the next
"用户家里养了一只叫奶黄的猫" doesn't write a fresh row when the same
fact already lives in the database. That fix only stops *new* duplicates;
the 18-row episodic and 7-row event tables that the user already has
keep their existing duplicates until somebody cleans them.

This script does that cleanup, with the same heuristics the worker uses:

- **Episodic**: cosine similarity ≥ ``--episodic-threshold`` (default 0.95;
  CLI is one-shot, so we run **stricter** than the on-line 0.90 threshold
  to minimize false-positives — better leave one extra "奶黄" row than
  delete a real distinct fact).
- **Event**: ``(type, normalized_title)`` equality + substring containment
  (≥ 4 chars), within ``--event-window-days`` (default 365 — historical
  scan, not the runtime 30-day window).

Behaviour
---------

For every user (or the one passed via ``--user-id``):

1. Pull all active episodic memories ordered by ``created_at`` ASC.
2. For each row, ANN-search the same user's *earlier* active rows; if
   the nearest neighbour's similarity ≥ threshold, the **later** row is
   marked as duplicate.
3. Every duplicate gets:

   - ``status = 'deprecated'`` flipped on the source row, AND
   - a ``MemoryDeprecation`` audit record inserted with
     ``action='deprecate'``, ``reason='dedup_cli'``, plus the original
     text and a hint pointing to the kept row.

4. Same for events, just keyed on normalized title within the window.

Use ``--dry-run`` to preview without writing anything. The summary is
emitted as JSON to stdout (one line per user is **not** the format —
final aggregate at the very end), with a human-friendly per-user
breakdown to stderr along the way.

Usage
-----

::

    docker compose exec backend python -m scripts.dedup_memories
    docker compose exec backend python -m scripts.dedup_memories --dry-run
    docker compose exec backend python -m scripts.dedup_memories \\
        --user-id 06b91a7f-... --episodic-threshold 0.93

Idempotent: re-running on a freshly-cleaned DB does no work (every
duplicate is already in ``status='deprecated'`` and excluded from the
ANN scan).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.domain.correction import MemoryDeprecation
from app.infra.db.factory import make_engine, make_sessionmaker
from app.infra.db.models import (
    EpisodicMemoryRow,
    EventRow,
    User,
)
from app.infra.repositories.deprecation_repo import DeprecationRepo
from app.infra.repositories.event_repo import _normalize_title

# --------------------------------------------------------------------------- #
# arg parsing
# --------------------------------------------------------------------------- #


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.dedup_memories",
        description=(
            "Sweep historical episodic + event duplicates and soft-delete them. "
            "Run inside the backend container; idempotent."
        ),
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="Restrict to a single user (default: every user with data)",
    )
    p.add_argument(
        "--episodic-threshold",
        type=float,
        default=0.95,
        help=(
            "Cosine similarity at or above which two episodic memories "
            "count as duplicates (default 0.95 — stricter than the "
            "on-line 0.90 to minimize false positives)"
        ),
    )
    p.add_argument(
        "--event-window-days",
        type=int,
        default=365,
        help=(
            "How far back to scan events for duplicates (default 365). "
            "Only same (type, normalized_title) within the window collide."
        ),
    )
    p.add_argument(
        "--no-episodic", action="store_true", help="Skip episodic dedup"
    )
    p.add_argument("--no-event", action="store_true", help="Skip event dedup")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen, but write nothing to the DB",
    )
    return p.parse_args()


# --------------------------------------------------------------------------- #
# Pure planning helpers (DB-free, easy to unit-test)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EventRecord:
    """Minimal event view used by the planner (avoids ORM dependency in tests)."""

    id: str
    type: str
    title: str
    created_at: datetime


@dataclass(frozen=True)
class EventDupPair:
    dup_id: str
    keep_id: str
    reason: str  # "exact" | "substring"


def plan_event_dedup(rows: list[EventRecord]) -> list[EventDupPair]:
    """Given events sorted by ``created_at`` ASC, return duplicate→keep pairs.

    Algorithm:
    - First occurrence of each ``(type, normalized_title)`` is kept.
    - Later events with the same key collapse onto it (``reason='exact'``).
    - Later events whose normalized title is a substring of an earlier
      keeper's title (or vice versa, with ≥ 4 char overlap) collapse
      onto that keeper (``reason='substring'``). This catches
      ``"宅家学习AI"`` vs ``"宅家学习AI 顺便看猫"``.

    Order matters: we walk in chronological order so the "earliest" row
    is the keeper, matching the deprecation audit semantics.
    """
    pairs: list[EventDupPair] = []
    keepers: list[tuple[str, str, str]] = []  # (id, type, normalized_title)
    for row in rows:
        norm = _normalize_title(row.title or "")
        if not norm:
            # 空标题:既不当 keeper,也不当重复(避免吞掉孤儿数据)
            continue
        # 1) 完全匹配(归一后)
        exact_keep = next(
            (kid for kid, kt, kn in keepers if kt == row.type and kn == norm),
            None,
        )
        if exact_keep is not None:
            pairs.append(EventDupPair(row.id, exact_keep, "exact"))
            continue
        # 2) 子串包含
        sub_keep = None
        for kid, kt, kn in keepers:
            if kt != row.type or not kn:
                continue
            shorter, longer = (kn, norm) if len(kn) <= len(norm) else (norm, kn)
            if len(shorter) >= 4 and shorter in longer:
                sub_keep = kid
                break
        if sub_keep is not None:
            pairs.append(EventDupPair(row.id, sub_keep, "substring"))
            continue
        keepers.append((row.id, row.type, norm))
    return pairs


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


@dataclass
class LayerReport:
    scanned: int = 0
    kept: int = 0
    deprecated: int = 0
    pairs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UserReport:
    user_id: str
    email: str | None = None
    episodic: LayerReport = field(default_factory=LayerReport)
    event: LayerReport = field(default_factory=LayerReport)


def _user_report_to_dict(r: UserReport) -> dict[str, Any]:
    def _layer(layer: LayerReport) -> dict[str, Any]:
        return {
            "scanned": layer.scanned,
            "kept": layer.kept,
            "deprecated": layer.deprecated,
            "pairs": layer.pairs,
        }

    return {
        "user_id": r.user_id,
        "email": r.email,
        "episodic": _layer(r.episodic),
        "event": _layer(r.event),
    }


# --------------------------------------------------------------------------- #
# DB-bound dedup
# --------------------------------------------------------------------------- #


async def _dedup_episodic_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    threshold: float,
    dry_run: bool,
) -> LayerReport:
    rep = LayerReport()

    stmt = (
        select(EpisodicMemoryRow)
        .where(
            EpisodicMemoryRow.user_id == user_id,
            EpisodicMemoryRow.status == "active",
        )
        .order_by(EpisodicMemoryRow.created_at.asc(), EpisodicMemoryRow.id.asc())
    )
    rows: list[EpisodicMemoryRow] = list(
        (await session.execute(stmt)).scalars().all()
    )
    rep.scanned = len(rows)
    if not rows:
        return rep

    deprecated_ids: set[str] = set()
    deprecation_repo = DeprecationRepo(session=session, user_id=user_id)

    for row in rows:
        if row.id in deprecated_ids:
            continue
        # ANN: 找早于本行、还没 deprecate、属同一用户的最近邻
        distance = EpisodicMemoryRow.embedding.cosine_distance(
            row.embedding
        ).label("distance")
        q = (
            select(EpisodicMemoryRow, distance)
            .where(
                EpisodicMemoryRow.user_id == user_id,
                EpisodicMemoryRow.status == "active",
                EpisodicMemoryRow.id != row.id,
                EpisodicMemoryRow.created_at < row.created_at,
            )
            .order_by(distance)
            .limit(1)
        )
        if deprecated_ids:
            q = q.where(EpisodicMemoryRow.id.notin_(deprecated_ids))
        hit = (await session.execute(q)).first()

        if hit is None:
            rep.kept += 1
            continue

        keep_row, dist = hit
        sim = max(0.0, min(1.0, 1.0 - float(dist) / 2.0))
        if sim < threshold:
            rep.kept += 1
            continue

        # 命中 → 当前 row 是后插的重复,deprecate 它
        rep.deprecated += 1
        rep.pairs.append(
            {
                "dup_id": row.id,
                "dup_text": row.text,
                "keep_id": keep_row.id,
                "keep_text": keep_row.text,
                "similarity": round(sim, 4),
            }
        )
        deprecated_ids.add(row.id)
        if not dry_run:
            await session.execute(
                update(EpisodicMemoryRow)
                .where(EpisodicMemoryRow.id == row.id)
                .values(status="deprecated")
            )
            await deprecation_repo.insert(
                MemoryDeprecation(
                    user_id=user_id,
                    source="episodic",
                    ref_id=row.id,
                    original_text=row.text,
                    new_text=None,
                    reason=(
                        f"dedup_cli: similar to {keep_row.id} "
                        f"(sim={sim:.3f} ≥ {threshold:.2f})"
                    ),
                    action="deprecate",
                )
            )

    return rep


async def _dedup_event_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    window_days: int,
    dry_run: bool,
) -> LayerReport:
    rep = LayerReport()
    cutoff = datetime.now(UTC) - timedelta(days=max(0, window_days))

    stmt = (
        select(EventRow)
        .where(
            EventRow.user_id == user_id,
            EventRow.status == "active",
            EventRow.created_at >= cutoff,
        )
        .order_by(EventRow.created_at.asc(), EventRow.id.asc())
    )
    rows: list[EventRow] = list((await session.execute(stmt)).scalars().all())
    rep.scanned = len(rows)
    if not rows:
        return rep

    records = [
        EventRecord(
            id=r.id,
            type=r.type,
            title=r.title or "",
            created_at=r.created_at,
        )
        for r in rows
    ]
    plan = plan_event_dedup(records)
    keep_total = len({r.id for r in records}) - len(plan)
    rep.kept = keep_total
    rep.deprecated = len(plan)

    by_id: dict[str, EventRow] = {r.id: r for r in rows}
    deprecation_repo = DeprecationRepo(session=session, user_id=user_id)
    for pair in plan:
        dup = by_id[pair.dup_id]
        keep = by_id[pair.keep_id]
        rep.pairs.append(
            {
                "dup_id": dup.id,
                "dup_type": dup.type,
                "dup_title": dup.title,
                "keep_id": keep.id,
                "keep_title": keep.title,
                "reason": pair.reason,
            }
        )
        if not dry_run:
            await session.execute(
                update(EventRow)
                .where(EventRow.id == dup.id)
                .values(status="deprecated")
            )
            await deprecation_repo.insert(
                MemoryDeprecation(
                    user_id=user_id,
                    source="event",
                    ref_id=dup.id,
                    original_text=f"{dup.type} / {dup.title}",
                    new_text=None,
                    reason=(
                        f"dedup_cli: {pair.reason} match "
                        f"with {keep.id} ('{keep.title}')"
                    ),
                    action="deprecate",
                )
            )
    return rep


async def _list_users(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str | None,
) -> list[tuple[str, str | None]]:
    async with sessionmaker() as s:
        stmt = select(User.id, User.email).where(User.is_active.is_(True))
        if user_id is not None:
            stmt = stmt.where(User.id == user_id)
        rows = (await s.execute(stmt)).all()
    return [(r.id, r.email) for r in rows]


# --------------------------------------------------------------------------- #
# Top-level driver
# --------------------------------------------------------------------------- #


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="web")
    sessionmaker = make_sessionmaker(engine)

    try:
        users = await _list_users(sessionmaker, user_id=args.user_id)
        if not users:
            print("(no matching users)", file=sys.stderr)
            return {"users": []}

        reports: list[UserReport] = []
        for uid, email in users:
            rep = UserReport(user_id=uid, email=email)
            async with sessionmaker() as session:
                if not args.no_episodic:
                    rep.episodic = await _dedup_episodic_for_user(
                        session,
                        user_id=uid,
                        threshold=args.episodic_threshold,
                        dry_run=args.dry_run,
                    )
                if not args.no_event:
                    rep.event = await _dedup_event_for_user(
                        session,
                        user_id=uid,
                        window_days=args.event_window_days,
                        dry_run=args.dry_run,
                    )
                if not args.dry_run:
                    await session.commit()
                else:
                    await session.rollback()

            _print_user_summary(rep, dry_run=args.dry_run)
            reports.append(rep)

        return {
            "users": [_user_report_to_dict(r) for r in reports],
            "totals": _aggregate_totals(reports),
            "dry_run": args.dry_run,
        }
    finally:
        await engine.dispose()


def _print_user_summary(rep: UserReport, *, dry_run: bool) -> None:
    label = "[DRY-RUN] " if dry_run else ""
    who = f"{rep.email or '(no email)'} ({rep.user_id})"
    print(f"{label}── {who}", file=sys.stderr)
    print(
        f"   episodic: scanned={rep.episodic.scanned} "
        f"kept={rep.episodic.kept} deprecated={rep.episodic.deprecated}",
        file=sys.stderr,
    )
    for pair in rep.episodic.pairs[:10]:
        print(
            f"     • dup={pair['dup_id']} sim={pair['similarity']} → "
            f"keep={pair['keep_id']}: {pair['dup_text'][:40]}",
            file=sys.stderr,
        )
    if len(rep.episodic.pairs) > 10:
        print(
            f"     • … and {len(rep.episodic.pairs) - 10} more",
            file=sys.stderr,
        )
    print(
        f"   event:    scanned={rep.event.scanned} "
        f"kept={rep.event.kept} deprecated={rep.event.deprecated}",
        file=sys.stderr,
    )
    for pair in rep.event.pairs[:10]:
        print(
            f"     • dup={pair['dup_id']} ({pair['reason']}) "
            f"{pair['dup_type']}/'{pair['dup_title']}' → "
            f"keep={pair['keep_id']} '{pair['keep_title']}'",
            file=sys.stderr,
        )
    if len(rep.event.pairs) > 10:
        print(
            f"     • … and {len(rep.event.pairs) - 10} more",
            file=sys.stderr,
        )


def _aggregate_totals(reports: list[UserReport]) -> dict[str, Any]:
    epi_scanned = sum(r.episodic.scanned for r in reports)
    epi_dep = sum(r.episodic.deprecated for r in reports)
    evt_scanned = sum(r.event.scanned for r in reports)
    evt_dep = sum(r.event.deprecated for r in reports)
    return {
        "episodic": {"scanned": epi_scanned, "deprecated": epi_dep},
        "event": {"scanned": evt_scanned, "deprecated": evt_dep},
        "users": len(reports),
    }


def main() -> int:
    args = _parse_args()
    try:
        out = asyncio.run(_run(args))
    except Exception as exc:  # surface root cause but keep exit code informative
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
