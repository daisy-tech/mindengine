"""Load synthetic eval cases from on-disk JSON.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.1 + §2.2.

Two sets ship under ``backend/eval/cases/``:
- ``smoke_cases.json`` (≈20 cases, 2-3 min)
- ``full_cases.json`` (50+ cases, 5-10 min)

Both files share the same shape: a list of ``EvalCase``-compatible dicts.
A schema mismatch raises ``CaseLoadError`` rather than silently dropping
the bad case — eval should be loud about its inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from app.domain.eval import EvalCase

DEFAULT_CASE_FILES: tuple[str, ...] = (
    "smoke_cases.json",
    "full_cases.json",
)


class CaseLoadError(RuntimeError):
    """Raised when a case file is malformed or missing."""


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load every case in ``path``. Raises ``CaseLoadError`` on bad data."""
    p = Path(path)
    if not p.exists():
        raise CaseLoadError(f"case file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseLoadError(f"invalid JSON in {p}: {exc}") from exc

    if isinstance(raw, dict) and "cases" in raw:
        raw = raw["cases"]
    if not isinstance(raw, list):
        raise CaseLoadError(f"{p} must contain a list of cases (or {{cases: []}})")

    cases: list[EvalCase] = []
    for i, entry in enumerate(raw):
        try:
            cases.append(EvalCase.model_validate(entry))
        except ValidationError as exc:
            raise CaseLoadError(f"{p} case#{i}: {exc}") from exc
    return cases


def list_case_files(directory: str | Path) -> list[Path]:
    """Return every ``*.json`` under ``directory`` in a stable order."""
    d = Path(directory)
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def load_named_set(directory: str | Path, name: str) -> list[EvalCase]:
    """Convenience: load by short name (e.g. ``"smoke"`` -> ``smoke_cases.json``)."""
    fname = f"{name}_cases.json" if not name.endswith(".json") else name
    return load_cases(Path(directory) / fname)


def iter_case_ids(cases: Iterable[EvalCase]) -> list[str]:
    return [c.id for c in cases]
