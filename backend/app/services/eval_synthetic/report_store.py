"""Filesystem persistence for synthetic eval reports.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.5 (落盘 + 复看,与 chat-audit
对齐的工程做法).

Each ``POST /api/eval/synthetic/{name}/start`` produces a report dict
(``reporter.build_report`` shape with ``schema=synthetic_run_v1``). We
write it as one JSON file under::

    {root}/{user_id}/{run_id}.json

so a user can later GET the same report by ``run_id`` without re-running
20 / 55 LLM calls. The ``run_id`` and ``user_id`` segments are validated
against the same regex used by chat-review (``[A-Za-z0-9_\\-]+``); any
other characters cause ``ValueError`` — same path-traversal guard pattern
as ``eval_chat_review.store`` (lesson 4.1).

This module deliberately mirrors ``eval_chat_review.store`` rather than
sharing it: the on-disk shape ("one report per case set per run") is
different enough that one shared API would be confusing. They share the
``safe_id_segment`` regex through reuse.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe(value: str, *, label: str) -> str:
    if not value or not _SAFE_SEGMENT.match(value):
        raise ValueError(f"{label!r} contains unsafe characters: {value!r}")
    return value


def _resolve_path(
    root: str | os.PathLike[str], user_id: str, run_id: str
) -> Path:
    user = _safe(user_id, label="user_id")
    rid = _safe(run_id, label="run_id")
    base = Path(root).resolve()
    target = (base / user / f"{rid}.json").resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:  # pragma: no cover — belt-and-braces
        raise ValueError(
            f"resolved path escaped root: {target} not under {base}"
        ) from exc
    return target


def save_report(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    run_id: str,
    report: dict[str, Any],
) -> Path:
    """Write the report dict to disk; returns the resolved path."""
    target = _resolve_path(root, user_id, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    target.write_text(payload, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(target, 0o644)
    return target


def load_report(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    target = _resolve_path(root, user_id, run_id)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def delete_report(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    run_id: str,
) -> bool:
    target = _resolve_path(root, user_id, run_id)
    if not target.exists():
        return False
    target.unlink()
    return True


@dataclass(frozen=True)
class StoredReportSummary:
    """One row in the "history" list. Same lightweight shape returned
    by :func:`list_reports`; the heavy ``cases`` array stays on disk."""

    run_id: str
    run_type: str
    finished_at: str
    total: int
    passed: int
    pass_rate: float


def _summarize(report: dict[str, Any], run_id: str) -> StoredReportSummary:
    return StoredReportSummary(
        run_id=run_id,
        run_type=str(report.get("run_type") or ""),
        finished_at=str(report.get("finished_at") or ""),
        total=int(report.get("total") or 0),
        passed=int(report.get("passed") or 0),
        pass_rate=float(report.get("pass_rate") or 0.0),
    )


def list_reports(
    *,
    root: str | os.PathLike[str],
    user_id: str,
) -> list[StoredReportSummary]:
    """Walk ``{root}/{user_id}/*.json`` and return summaries (newest first).

    Files that fail to parse are silently skipped — the API surface
    should remain robust if a stale file got corrupted.
    """
    user = _safe(user_id, label="user_id")
    base = Path(root).resolve() / user
    if not base.exists():
        return []
    out: list[StoredReportSummary] = []
    # Skip macOS / NFS AppleDouble files (already learned this lesson once).
    for path in sorted(base.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(_summarize(report, path.stem))
    out.sort(key=lambda s: s.finished_at, reverse=True)
    return out


def safe_iter_users(root: str | os.PathLike[str]) -> Iterable[str]:
    """Iterate user-id directories under root (matching the safe regex)."""
    base = Path(root).resolve()
    if not base.exists():
        return []
    out: list[str] = []
    for child in base.iterdir():
        if child.is_dir() and _SAFE_SEGMENT.match(child.name):
            out.append(child.name)
    return out
