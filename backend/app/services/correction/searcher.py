"""Cross-layer candidate search for correction targets.

Per docs/rebuild/06-Subsystem-Correction.md §4 Step 2.

Given a ``CorrectionTarget.ref``, scan all 4 memory layers for items
whose text contains the ref (case-insensitive substring) — that's the
v1 heuristic; v2 may swap in pgvector neighbour search (doc §6.5).

The searcher is intentionally cheap: no LLM, just a SQL pass per layer.
The expensive judging happens in ``CorrectionJudge``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.domain.correction import CorrectionCandidate, CorrectionTarget
from app.services.protocols import (
    EpisodicRepository,
    EventRepository,
    ProfileRepository,
    RelationshipRepository,
)


@dataclass
class CandidateBundle:
    """A flat list of candidates plus per-layer counts (handy in logs)."""

    items: list[CorrectionCandidate] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def __iter__(self) -> Iterable[CorrectionCandidate]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


@dataclass
class CorrectionCandidateSearcher:
    profile_repo: ProfileRepository
    event_repo: EventRepository
    episodic_repo: EpisodicRepository
    relationship_repo: RelationshipRepository
    candidate_limit: int = 10

    async def search(self, targets: list[CorrectionTarget]) -> CandidateBundle:
        """Return up to ``candidate_limit`` candidates total across layers.

        Items are deduplicated by (source, ref_id) so the same row can't
        be judged twice when it matches multiple targets.
        """
        if not targets:
            return CandidateBundle()

        seen: set[tuple[str, str]] = set()
        bundle = CandidateBundle()

        for target in targets:
            ref = target.ref.strip()
            if not ref:
                continue
            await self._scan_episodic(ref, bundle, seen)
            await self._scan_events(ref, bundle, seen)
            await self._scan_profile(ref, bundle, seen)
            await self._scan_relationships(ref, bundle, seen)
            if len(bundle.items) >= self.candidate_limit:
                break

        bundle.items = bundle.items[: self.candidate_limit]
        return bundle

    # ─── per-layer scans ─────────────────────────────────────────

    async def _scan_episodic(
        self,
        ref: str,
        bundle: CandidateBundle,
        seen: set[tuple[str, str]],
    ) -> None:
        # ``list_recent`` is enough for v1 — episodic table is capped at
        # ~2k per user (doc 05 §5.4) and substring matches are cheap.
        # Using vector search here would flag near-neighbors that may
        # not actually mention the ref; we want literal containment.
        rows = await self.episodic_repo.list_recent(limit=200)
        needle = ref.lower()
        added = 0
        for row in rows:
            if row.text and needle in row.text.lower():
                key = ("episodic", row.id)
                if key in seen:
                    continue
                seen.add(key)
                bundle.items.append(
                    CorrectionCandidate(
                        source="episodic",
                        ref_id=row.id,
                        text=row.text,
                    )
                )
                added += 1
        bundle.counts["episodic"] = bundle.counts.get("episodic", 0) + added

    async def _scan_events(
        self,
        ref: str,
        bundle: CandidateBundle,
        seen: set[tuple[str, str]],
    ) -> None:
        rows = await self.event_repo.list_recent(limit=200)
        needle = ref.lower()
        added = 0
        for row in rows:
            blob = f"{row.title}\n{row.content}".lower()
            if needle in blob:
                key = ("event", row.id)
                if key in seen:
                    continue
                seen.add(key)
                bundle.items.append(
                    CorrectionCandidate(
                        source="event",
                        ref_id=row.id,
                        text=f"{row.title}: {row.content}",
                    )
                )
                added += 1
        bundle.counts["event"] = bundle.counts.get("event", 0) + added

    async def _scan_profile(
        self,
        ref: str,
        bundle: CandidateBundle,
        seen: set[tuple[str, str]],
    ) -> None:
        profile = await self.profile_repo.get()
        if profile is None:
            return
        needle = ref.lower()
        added = 0
        # Inspect the structured override fields — they're the ones a
        # correction is most likely to be about (name / location / etc.).
        flat: dict[str, str | None] = {
            "basic.name": profile.basic.name,
            "basic.birth_year": (
                str(profile.basic.birth_year)
                if profile.basic.birth_year is not None
                else None
            ),
            "basic.location": profile.basic.location,
            "occupation.title": profile.occupation.title,
            "occupation.industry": profile.occupation.industry,
            "occupation.level": profile.occupation.level,
            "family_structure.structure": profile.family_structure.structure,
        }
        for path, value in flat.items():
            if value and needle in value.lower():
                key = ("profile", path)
                if key in seen:
                    continue
                seen.add(key)
                bundle.items.append(
                    CorrectionCandidate(
                        source="profile",
                        ref_id=path,
                        text=f"{path}={value}",
                    )
                )
                added += 1
        # Interests: listed as a free-form list — match per item.
        for idx, hobby in enumerate(profile.interests):
            if hobby and needle in hobby.lower():
                ref_id = f"interests[{idx}]"
                key = ("profile", ref_id)
                if key in seen:
                    continue
                seen.add(key)
                bundle.items.append(
                    CorrectionCandidate(
                        source="profile",
                        ref_id=ref_id,
                        text=f"interest={hobby}",
                    )
                )
                added += 1
        bundle.counts["profile"] = bundle.counts.get("profile", 0) + added

    async def _scan_relationships(
        self,
        ref: str,
        bundle: CandidateBundle,
        seen: set[tuple[str, str]],
    ) -> None:
        # Two queries: name match (cheap exact) + role match via the
        # ``find_by_name`` helper on a couple of common variants.
        rows = await self.relationship_repo.find_by_name(ref)
        added = 0
        for row in rows:
            key = ("relationship", row.id)
            if key in seen:
                continue
            seen.add(key)
            bundle.items.append(
                CorrectionCandidate(
                    source="entity",  # mapped to MemoryDeprecation.source enum
                    ref_id=row.id,
                    text=f"{row.role}: {row.name}",
                )
            )
            added += 1
        bundle.counts["relationship"] = bundle.counts.get("relationship", 0) + added
