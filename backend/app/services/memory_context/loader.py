"""Reads from the four-layer memory based on a MemoryRoute.

Per docs/rebuild/05-Subsystem-Memory-Layers.md §8.

Outputs a fully-populated :class:`MemoryContext` with each item already
tagged with :class:`MemoryUsage` so the prompt composer doesn't have to
re-derive the policy.

Banned-entity + deprecation filtering happens here, NOT in the composer.
That keeps the composer's job purely textual (lesson 5.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.memory import (
    MemoryContext,
    Profile,
    Relationship,
    RoutedMemoryItem,
    SnapshotStats,
)
from app.domain.route import (
    EventPolicy,
    Intent,
    MemoryRoute,
    MemoryUsage,
)
from app.services.protocols import (
    BannedEntityRepository,
    DeprecationRepository,
    EpisodicRepository,
    EventRepository,
    ProfileRepository,
    RelationshipRepository,
)


@dataclass
class MemoryContextLoader:
    profile_repo: ProfileRepository
    event_repo: EventRepository
    episodic_repo: EpisodicRepository
    relationship_repo: RelationshipRepository
    banned_repo: BannedEntityRepository
    deprecation_repo: DeprecationRepository
    episodic_search_limit: int = 8

    async def load(self, route: MemoryRoute) -> MemoryContext:
        ctx = MemoryContext()
        layers = set(route.load_layers)

        # ─── profile ────────────────────────────────────────────────
        if "profile_basic" in layers or "profile" in layers:
            profile = await self.profile_repo.get()
            full = "profile" in layers
            ctx.stable_profile.extend(self._profile_items(profile, full=full))
            ctx.snapshot_stats.profile_total = len(ctx.stable_profile)

        # ─── relationships ──────────────────────────────────────────
        if "relationships" in layers:
            rels = await self.relationship_repo.list_for_intent(route)
            ctx.relevant_relationships.extend(self._relationship_items(rels))
            ctx.snapshot_stats.relationship_total = len(ctx.relevant_relationships)

        # ─── events ─────────────────────────────────────────────────
        if "events" in layers and route.event_policy != EventPolicy.NONE:
            events = await self.event_repo.list_recent(limit=8)
            usage = (
                MemoryUsage.BACKGROUND_ONLY
                if route.event_policy == EventPolicy.BACKGROUND_PAIN_POINTS
                else MemoryUsage.EXPLICIT_OK
            )
            for e in events:
                item = RoutedMemoryItem(
                    source="event",
                    ref_id=e.id,
                    text=f"[{e.type}] {e.title}：{e.content}",
                    usage=usage.value,
                )
                if usage is MemoryUsage.BACKGROUND_ONLY:
                    ctx.background_only.append(item)
                else:
                    ctx.relevant_events.append(item)
            ctx.snapshot_stats.event_total = len(events)

        # ─── episodic ───────────────────────────────────────────────
        if "episodic" in layers:
            banned, deprecated_ids = await self._load_filters()
            ctx.snapshot_stats.banned_entities = sorted(banned)
            hits = await self.episodic_repo.search(
                route.query or "",
                limit=self.episodic_search_limit,
            )
            kept: list[RoutedMemoryItem] = []
            for hit in hits:
                if hit.id in deprecated_ids:
                    continue  # excluded by correction (lesson 5.4)
                if any(b in hit.text for b in banned):
                    continue  # excluded by banned-entities (doc 06 §6.4)
                kept.append(
                    RoutedMemoryItem(
                        source="episodic",
                        ref_id=hit.id,
                        text=hit.text,
                        usage=self._episodic_usage(route).value,
                        score=hit.score,
                    )
                )
            ctx.relevant_memories.extend(kept[: max(1, route.max_explicit_memories or 3)])
            ctx.snapshot_stats.episodic_total = len(kept)

        return ctx

    # ─── helpers ─────────────────────────────────────────────────

    async def _load_filters(self) -> tuple[set[str], set[str]]:
        banned_rows = await self.banned_repo.list()
        banned = {b.entity for b in banned_rows}
        deprecated_ids = await self.deprecation_repo.list_episodic_ids()
        return banned, deprecated_ids

    def _episodic_usage(self, route: MemoryRoute) -> MemoryUsage:
        # Sensitive intents: keep memories as background only (doc 04 §6).
        if route.sensitive_mode:
            return MemoryUsage.BACKGROUND_ONLY
        # Memory challenge / self-summary: explicit ok.
        if route.intent in {Intent.MEMORY_CHALLENGE, Intent.SELF_SUMMARY}:
            return MemoryUsage.EXPLICIT_OK
        return MemoryUsage.FOLLOW_UP_ONCE

    def _profile_items(self, profile: Profile | None, *, full: bool) -> list[RoutedMemoryItem]:
        if profile is None:
            return []
        items: list[RoutedMemoryItem] = []
        if profile.basic.name:
            items.append(self._profile_item("basic.name", f"姓名：{profile.basic.name}"))
        if profile.basic.location:
            items.append(self._profile_item("basic.location", f"所在地：{profile.basic.location}"))
        if profile.basic.birth_year:
            items.append(self._profile_item("basic.birth_year", f"出生年：{profile.basic.birth_year}"))
        if not full:
            return items
        if profile.occupation.title:
            items.append(self._profile_item("occupation", f"职业：{profile.occupation.title}"))
        if profile.interests:
            items.append(
                self._profile_item("interests", f"兴趣：{', '.join(profile.interests[:6])}")
            )
        if profile.family_structure.structure:
            items.append(
                self._profile_item("family", f"家庭：{profile.family_structure.structure}")
            )
        return items

    @staticmethod
    def _profile_item(key: str, text: str) -> RoutedMemoryItem:
        return RoutedMemoryItem(
            source="profile",
            ref_id=f"profile:{key}",
            text=text,
            usage=MemoryUsage.EXPLICIT_OK.value,
        )

    @staticmethod
    def _relationship_items(rels: list[Relationship]) -> list[RoutedMemoryItem]:
        items: list[RoutedMemoryItem] = []
        for r in rels[:6]:
            attrs = "，".join(f"{k}={v}" for k, v in (r.attributes or {}).items() if v)
            text = f"{r.role}：{r.name}" + (f"（{attrs}）" if attrs else "")
            items.append(
                RoutedMemoryItem(
                    source="relationship",
                    ref_id=r.id,
                    text=text,
                    usage=MemoryUsage.EXPLICIT_OK.value,
                )
            )
        return items


def stats_only(ctx: MemoryContext) -> SnapshotStats:
    """Convenience accessor — returns the stats independently of the items
    so callers (composer / eval) can serialize without dragging full text."""
    return ctx.snapshot_stats
