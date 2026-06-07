"""Filesystem persistence for review packs.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §4.

Layout (under ``settings.eval_chat_reviews_dir``):

    {root}/{user_id}/{conv_id}.json

The ``user_id`` and ``conv_id`` segments are strictly validated to match
``[A-Za-z0-9_-]+``. Any other characters cause ``ValueError`` — this is
the path-traversal guard called out in doc §4.1. Files are written with
mode ``0o644`` so an NFS-mounted Mac can read them (lesson 4.3).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def safe_id_segment(value: str, *, label: str = "id") -> str:
    """Validate and return ``value`` if it matches the safe regex.

    Raises ``ValueError`` otherwise — the caller should map this to a
    400 / 422.
    """
    if not value or not _SAFE_SEGMENT.match(value):
        raise ValueError(f"{label!r} contains unsafe characters: {value!r}")
    return value


def _resolve_path(root: str | os.PathLike[str], user_id: str, conv_id: str) -> Path:
    user = safe_id_segment(user_id, label="user_id")
    conv = safe_id_segment(conv_id, label="conv_id")
    base = Path(root).resolve()
    target = (base / user / f"{conv}.json").resolve()
    # Belt-and-braces: even after the regex, ensure the resolved path
    # remains inside the configured root.
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"resolved path escaped root: {target} not under {base}"
        ) from exc
    return target


def save_review(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    conversation_id: str,
    review: dict[str, Any],
) -> Path:
    """Write the review pack to disk (overwrite). Returns final path."""
    target = _resolve_path(root, user_id, conversation_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(review, ensure_ascii=False, indent=2)
    target.write_text(payload, encoding="utf-8")
    # NFS may strip chmod; that's OK — content is readable already.
    with contextlib.suppress(OSError):
        os.chmod(target, 0o644)
    return target


def load_review(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    conversation_id: str,
) -> dict[str, Any] | None:
    target = _resolve_path(root, user_id, conversation_id)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def delete_review(
    *,
    root: str | os.PathLike[str],
    user_id: str,
    conversation_id: str,
) -> bool:
    target = _resolve_path(root, user_id, conversation_id)
    if not target.exists():
        return False
    target.unlink()
    return True


@dataclass(frozen=True)
class StoredSummary:
    conversation_id: str
    evaluated_at: str
    turns_total: int
    evaluable_turns: int
    final_ok_rate: float
    counters: dict[str, int]


def _summarize_review(review: dict[str, Any], conv_id: str) -> StoredSummary:
    block = review.get("review") or {}
    counters = dict(block.get("counters") or {})
    return StoredSummary(
        conversation_id=conv_id,
        evaluated_at=str(review.get("evaluated_at") or ""),
        turns_total=int(block.get("evaluable_turns") or len(review.get("turns") or [])),
        evaluable_turns=int(block.get("evaluable_turns") or 0),
        final_ok_rate=float(block.get("final_ok_rate") or 0.0),
        counters=counters,
    )


def list_stored_summaries(
    *,
    root: str | os.PathLike[str],
    user_id: str,
) -> list[StoredSummary]:
    """Walk ``{root}/{user_id}/*.json`` and return summaries.

    Files that fail to parse are silently skipped — the API surface
    should remain robust if a stale review file got corrupted.
    """
    user = safe_id_segment(user_id, label="user_id")
    base = Path(root).resolve() / user
    if not base.exists():
        return []
    out: list[StoredSummary] = []
    for path in sorted(base.glob("*.json")):
        try:
            review = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        conv_id = path.stem
        out.append(_summarize_review(review, conv_id))
    out.sort(key=lambda s: s.evaluated_at, reverse=True)
    return out


def reset_user_dir(
    *,
    root: str | os.PathLike[str],
    user_id: str,
) -> int:
    """Test helper / dev-only: remove every review file for a user.

    Returns the number of files removed. Intentionally not exposed via
    API — used by the seed_persona endpoint and by tests.
    """
    user = safe_id_segment(user_id, label="user_id")
    base = Path(root).resolve() / user
    if not base.exists():
        return 0
    paths = list(base.glob("*.json"))
    for p in paths:
        p.unlink()
    return len(paths)


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


def now_iso() -> str:
    """Helper used in tests + the API to stamp evaluated_at."""
    return datetime.now(UTC).isoformat()


def cleanup_root(root: str | os.PathLike[str]) -> None:
    """Test helper: rm -rf the entire reviews root."""
    base = Path(root).resolve()
    if base.exists():
        shutil.rmtree(base)
