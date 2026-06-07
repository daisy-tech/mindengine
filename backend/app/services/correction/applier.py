"""Cross-layer applier for correction judgements.

Per docs/rebuild/06-Subsystem-Correction.md §4 Step 4 + §6.

Given a list of ``JudgementOutcome`` plus a list of cleaned banned
entities, this module:

- Soft-deletes the underlying memory row (``status='deprecated'``)
  for episodic + event candidates whose action == ``deprecate`` /
  ``update``.
- Patches the profile JSONB blob for profile candidates (clearing or
  replacing a single field path), and records a ``UserCorrection``
  audit entry on the profile itself.
- Always records a row in ``memory_deprecations`` (incl. ``audit_only``).
- Idempotently inserts banned entities.

The applier is intentionally synchronous from the worker's POV (await
each repo call in turn). We rely on Postgres MVCC + the worker's
session.commit() at the boundary, not redis_lock (doc 11 v1.1).

The implementation is repo-Protocol-driven, so unit tests can pass in
fakes — see ``tests/unit/services/correction/test_applier.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.correction import (
    BannedEntity,
    CorrectionJudgement,
    MemoryDeprecation,
)
from app.domain.memory import UserCorrection
from app.services.correction.judge import JudgementOutcome
from app.services.protocols import (
    BannedEntityRepository,
    DeprecationRepository,
    EpisodicRepository,
    EventRepository,
    ProfileRepository,
)


@dataclass
class ApplyOutcome:
    """Aggregated outcome of one correction-cleanup invocation.

    The numbers are meant for logs / metrics; the data itself is in
    Postgres now.
    """

    deprecations_inserted: int = 0
    episodic_soft_deleted: int = 0
    event_soft_deleted: int = 0
    profile_fields_patched: int = 0
    banned_inserted: int = 0
    audit_only: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class CorrectionApplier:
    """Applies a batch of judged candidates + banned-entity list."""

    profile_repo: ProfileRepository
    event_repo: EventRepository
    episodic_repo: EpisodicRepository
    banned_repo: BannedEntityRepository
    deprecation_repo: DeprecationRepository

    async def apply(
        self,
        *,
        outcomes: Sequence[JudgementOutcome],
        banned: Sequence[str],
        user_id: str,
        conversation_id: str | None,
        correction_message_id: str | None,
        user_correction_text: str,
    ) -> ApplyOutcome:
        out = ApplyOutcome()

        for oc in outcomes:
            await self._apply_one(
                outcome=oc,
                user_id=user_id,
                conversation_id=conversation_id,
                correction_message_id=correction_message_id,
                user_correction_text=user_correction_text,
                acc=out,
            )

        # Banned entities — separate from per-candidate deprecations.
        if banned:
            entities: list[BannedEntity] = []
            for ent in banned:
                if not ent:
                    continue
                try:
                    entities.append(
                        BannedEntity(
                            user_id=user_id,
                            entity=ent,
                            reason=f"correction:{correction_message_id or ''}",
                        )
                    )
                except ValueError:
                    # Defensive: clean_banned_entity should have caught these
                    # already, but the BannedEntity validator can still reject
                    # something we missed. Drop silently.
                    out.notes.append(f"banned_validation_failed:{ent!r}")
            if entities:
                added = await self.banned_repo.add_many(entities)
                out.banned_inserted += int(added)

        return out

    # ─── per-candidate dispatch ─────────────────────────────────

    async def _apply_one(
        self,
        *,
        outcome: JudgementOutcome,
        user_id: str,
        conversation_id: str | None,
        correction_message_id: str | None,
        user_correction_text: str,
        acc: ApplyOutcome,
    ) -> None:
        cand = outcome.candidate
        judg = outcome.judgement

        # Always log a deprecation row (audit_only included — doc §4).
        dep = MemoryDeprecation(
            user_id=user_id,
            source=cand.source,
            ref_id=cand.ref_id,
            original_text=cand.text,
            new_text=judg.new_text,
            reason=judg.reason,
            correction_conversation_id=conversation_id,
            correction_turn_id=correction_message_id,
            llm_confidence=judg.confidence,
            action=judg.action,
        )
        await self.deprecation_repo.insert(dep)
        acc.deprecations_inserted += 1

        if judg.action == "audit_only":
            acc.audit_only += 1
            return

        if cand.source == "episodic":
            await self.episodic_repo.soft_delete(cand.ref_id, judg.reason or "correction")
            acc.episodic_soft_deleted += 1
        elif cand.source == "event":
            await self.event_repo.deprecate(cand.ref_id, judg.reason or "correction")
            acc.event_soft_deleted += 1
        elif cand.source == "profile":
            patched = await self._apply_profile(
                ref_id=cand.ref_id,
                judgement=judg,
                user_correction_text=user_correction_text,
            )
            if patched:
                acc.profile_fields_patched += 1
            else:
                acc.skipped += 1
                acc.notes.append(f"profile_unknown_path:{cand.ref_id}")
        elif cand.source == "entity":
            # Relationship matches end up here. v1 only audits + bans;
            # we do not flip the relationship row's status (doc §9 leaves
            # this out of scope). The banned_entities insert below is the
            # actual protection against future re-emergence.
            acc.audit_only += 1
            acc.notes.append(f"entity_audit:{cand.ref_id}")
        else:
            acc.skipped += 1
            acc.notes.append(f"unknown_source:{cand.source}")

    # ─── profile patching ───────────────────────────────────────

    async def _apply_profile(
        self,
        *,
        ref_id: str,
        judgement: CorrectionJudgement,
        user_correction_text: str,
    ) -> bool:
        """Apply ``deprecate`` / ``update`` to a profile field path.

        Recognised paths (mirrors searcher.py):
            basic.name, basic.birth_year, basic.location,
            occupation.title, occupation.industry, occupation.level,
            family_structure.structure,
            interests[<idx>]

        Unknown paths are no-ops (returns False) — the deprecation row
        already records that we tried, so this is auditable.
        """
        profile = await self.profile_repo.get()
        if profile is None:
            return False

        new_value: Any = judgement.new_text if judgement.action == "update" else None

        path = ref_id

        # Interests: indexed list. ``deprecate`` removes; ``update`` swaps.
        if path.startswith("interests[") and path.endswith("]"):
            try:
                idx = int(path[len("interests[") : -1])
            except ValueError:
                return False
            interests = list(profile.interests)
            if not (0 <= idx < len(interests)):
                return False
            removed = interests.pop(idx)
            if judgement.action == "update" and new_value:
                interests.insert(idx, str(new_value))
            profile = profile.model_copy(update={"interests": interests})
            self._record_correction(profile, path, removed, new_value, judgement.reason)
            await self.profile_repo.upsert(profile)
            return True

        # Flat dotted path: section.field.
        parts = path.split(".")
        if len(parts) != 2:
            return False
        section, field_name = parts
        section_obj = getattr(profile, section, None)
        if section_obj is None or not hasattr(section_obj, field_name):
            return False
        old_value = getattr(section_obj, field_name)
        coerced = self._coerce_field(field_name, new_value)
        new_section = section_obj.model_copy(update={field_name: coerced})
        profile = profile.model_copy(update={section: new_section})
        self._record_correction(
            profile, path, old_value, coerced, judgement.reason
        )
        await self.profile_repo.upsert(profile)
        return True

    @staticmethod
    def _coerce_field(field_name: str, raw_value: Any) -> Any:
        """``birth_year`` is the only int field — coerce strings to int.

        Anything else stays as the LLM's raw string (or None for deprecate).
        """
        if raw_value in (None, ""):
            return None
        if field_name == "birth_year":
            try:
                return int(str(raw_value).strip())
            except (TypeError, ValueError):
                return None
        return str(raw_value).strip() or None

    @staticmethod
    def _record_correction(
        profile: Any,
        path: str,
        old_value: Any,
        new_value: Any,
        reason: str,
    ) -> None:
        _ = reason  # not part of the persisted audit row schema
        # UserCorrection field validators may reject some shapes; we don't
        # want a malformed audit entry to abort the apply itself.
        with contextlib.suppress(Exception):
            profile.user_corrections.append(
                UserCorrection(
                    field_path=path,
                    old_value=None if old_value is None else str(old_value),
                    new_value=None if new_value is None else str(new_value),
                    happened_at=datetime.now(UTC),
                )
            )
