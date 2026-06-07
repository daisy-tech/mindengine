"""Pure merge logic for Profile (no IO).

Per docs/rebuild/05-Subsystem-Memory-Layers.md §3.3 + lesson 4.1:
- Override fields: latest non-None value wins. Conflicts get logged into
  ``user_corrections`` for audit.
- Accumulative fields: union + dedup (case-folded) with a hard cap to
  prevent runaway growth.
- Anything else gets dropped — extraction LLMs are noisy and we'd rather
  miss a field than store garbage.

The merger never decides whether to PERSIST the merged profile; that is
the worker's job. The merger only computes a new ``Profile`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.memory import (
    BasicInfo,
    FamilyHint,
    Occupation,
    Profile,
    UserCorrection,
)

_INTERESTS_CAP = 30
_FAMILY_NOTES_CAP = 12
_EXTRA_KEY_CAP = 64

_TRUTHY_FIELD_PATHS: tuple[str, ...] = (
    "basic.name",
    "basic.birth_year",
    "basic.gender",
    "basic.location",
    "occupation.title",
    "occupation.industry",
    "occupation.level",
    "family_structure.structure",
)


@dataclass
class ProfileMerger:
    """Stateless service. Single instance per process is fine.

    Tunable constants (caps) are kept on the dataclass so eval / tests
    can construct a tighter merger when needed.
    """

    interests_cap: int = _INTERESTS_CAP
    family_notes_cap: int = _FAMILY_NOTES_CAP

    def merge(self, current: Profile | None, partial: dict[str, Any]) -> Profile:
        return merge_profile(
            current,
            partial,
            interests_cap=self.interests_cap,
            family_notes_cap=self.family_notes_cap,
        )


def merge_profile(
    current: Profile | None,
    partial: dict[str, Any],
    *,
    interests_cap: int = _INTERESTS_CAP,
    family_notes_cap: int = _FAMILY_NOTES_CAP,
) -> Profile:
    """Functional API used by both ProfileMerger and tests directly.

    ``partial`` may be any subset of the Profile shape (nested dicts
    allowed). Unknown top-level keys are dropped. ``current=None`` means
    "no profile yet" — we synthesize a fresh one keyed by ``user_id``
    found inside ``partial``.
    """
    if current is None:
        user_id = partial.get("user_id")
        if not user_id or not isinstance(user_id, str):
            raise ValueError(
                "merge_profile: when current=None, partial must include user_id"
            )
        current = Profile(user_id=user_id)

    new_basic = _merge_section(
        current.basic.model_dump(),
        partial.get("basic"),
        section="basic",
    )
    new_occupation = _merge_section(
        current.occupation.model_dump(),
        partial.get("occupation"),
        section="occupation",
    )
    new_family = _merge_family(current.family_structure, partial.get("family_structure"))

    new_interests = _merge_string_list(
        current.interests,
        partial.get("interests"),
        cap=interests_cap,
    )

    new_extra = _merge_extra(current.extra, partial.get("extra"))

    new_corrections = list(current.user_corrections)
    new_corrections.extend(_collect_corrections(current, partial))

    return Profile(
        schema_version=current.schema_version,
        user_id=current.user_id,
        basic=BasicInfo(**new_basic),
        interests=new_interests,
        occupation=Occupation(**new_occupation),
        family_structure=new_family,
        extra=new_extra,
        user_corrections=new_corrections,
        updated_at=datetime.now(UTC),
    )


# ─── helpers ──────────────────────────────────────────────────────


def _merge_section(
    current: dict[str, Any], incoming: Any, *, section: str
) -> dict[str, Any]:
    """Override-field merge: incoming non-None values win."""
    if not isinstance(incoming, dict):
        return dict(current)
    out = dict(current)
    for key, value in incoming.items():
        if value is None:
            continue
        full = f"{section}.{key}"
        if full not in _TRUTHY_FIELD_PATHS:
            # Field not in our explicit override list — drop it (lesson 4.1).
            continue
        out[key] = value
    return out


def _merge_family(current: FamilyHint, incoming: Any) -> FamilyHint:
    if not isinstance(incoming, dict):
        return current
    raw_structure = incoming.get("structure")
    new_structure = (
        raw_structure if isinstance(raw_structure, str) and raw_structure.strip()
        else current.structure
    )
    new_notes = _merge_string_list(
        current.notes, incoming.get("notes"), cap=_FAMILY_NOTES_CAP
    )
    return FamilyHint(structure=new_structure, notes=new_notes)


def _merge_string_list(
    current: list[str], incoming: Any, *, cap: int
) -> list[str]:
    out = list(current)
    seen = {s.strip().lower() for s in out}
    if not isinstance(incoming, list):
        return out
    for raw in incoming:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out[:cap]


def _merge_extra(current: dict[str, str], incoming: Any) -> dict[str, str]:
    out = dict(current)
    if not isinstance(incoming, dict):
        return out
    for k, v in incoming.items():
        if not isinstance(k, str) or not k:
            continue
        if len(k) > _EXTRA_KEY_CAP:
            continue
        if v is None:
            continue
        if not isinstance(v, str | int | float | bool):
            continue
        out[k] = str(v)
    return out


def _collect_corrections(
    current: Profile, partial: dict[str, Any]
) -> list[UserCorrection]:
    """Detect override-field changes and record them.

    Only triggered when ``partial`` carries a ``__source__: 'correction'``
    marker. Regular extraction shouldn't pollute the audit trail.
    """
    if partial.get("__source__") != "correction":
        return []
    happened = datetime.now(UTC)
    out: list[UserCorrection] = []
    for path in _TRUTHY_FIELD_PATHS:
        section, key = path.split(".", 1)
        incoming_section = partial.get(section)
        if not isinstance(incoming_section, dict):
            continue
        new_value = incoming_section.get(key)
        if new_value is None:
            continue
        old_value = getattr(getattr(current, section), key, None)
        if old_value == new_value:
            continue
        out.append(
            UserCorrection(
                field_path=path,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value),
                happened_at=happened,
            )
        )
    return out
