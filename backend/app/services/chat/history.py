"""Helpers for converting persisted message rows into LLM-ready dicts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class _RoleContent(Protocol):
    id: str
    role: str
    content: str


def history_to_messages(
    rows: Sequence[_RoleContent],
    *,
    exclude_message_id: str | None = None,
) -> list[dict[str, str]]:
    """Convert message rows to the OpenAI-style messages list.

    - Drops system messages (we always re-build the system text).
    - Drops the `exclude_message_id` (the just-inserted user message,
      which we re-append separately to give the LLM the full context).
    """
    out: list[dict[str, str]] = []
    for r in rows:
        if r.role == "system":
            continue
        if exclude_message_id is not None and r.id == exclude_message_id:
            continue
        if r.role not in {"user", "assistant"}:
            continue
        out.append({"role": r.role, "content": r.content})
    return out
