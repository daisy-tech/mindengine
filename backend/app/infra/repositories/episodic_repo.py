"""Episodic memory repository (Postgres + pgvector).

Per docs/rebuild/05-Subsystem-Memory-Layers.md §5 + 08-Data-Model.md §3.
- Add: takes raw text, embeds via injected EmbeddingClient, INSERTs row.
- Search: cosine ANN via the HNSW index, filtered by user_id + status='active'.
- Soft delete: status='deprecated' (no row removal — auditable).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import EpisodicHit, EpisodicMemory
from app.infra.db.models import EpisodicMemoryRow
from app.services.protocols import EmbeddingClient


@dataclass
class EpisodicRepo:
    session: AsyncSession
    user_id: str
    embedder: EmbeddingClient

    async def add(self, text: str, metadata: dict[str, Any]) -> str:
        if not text.strip():
            raise ValueError("EpisodicRepo.add: text must not be empty")
        mem_id = metadata.get("id") or str(uuid.uuid4())
        embedding = await self.embedder.embed(text)
        self.session.add(
            EpisodicMemoryRow(
                id=mem_id,
                user_id=self.user_id,
                text=text,
                embedding=embedding,
                status="active",
                source_message_id=metadata.get("source_message_id"),
                source=metadata.get("source"),
                schema_version=metadata.get("schema_version", 1),
            )
        )
        await self.session.flush()
        return mem_id

    async def search(self, query: str, *, limit: int = 10) -> list[EpisodicHit]:
        q_vec = await self.embedder.embed(query)
        # cosine_distance returns a distance in [0, 2]; convert to similarity in [0, 1].
        distance = EpisodicMemoryRow.embedding.cosine_distance(q_vec).label("distance")
        stmt = (
            select(EpisodicMemoryRow, distance)
            .where(
                EpisodicMemoryRow.user_id == self.user_id,
                EpisodicMemoryRow.status == "active",
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        hits: list[EpisodicHit] = []
        for row, dist in rows:
            sim = max(0.0, min(1.0, 1.0 - float(dist) / 2.0))
            hits.append(EpisodicHit(id=row.id, text=row.text, score=sim))
        return hits

    async def find_similar(
        self,
        text: str,
        *,
        threshold: float = 0.90,
    ) -> EpisodicHit | None:
        """Return the most similar **active** episodic memory if its
        cosine similarity is ≥ ``threshold``, else ``None``.

        Used by the worker to skip inserting "用户养了一只奶黄的猫" again
        when a near-identical fact already exists. See doc 05 §5.4 ("不
        要无脑追加,先 ANN 一次"); this is the second-half of the lesson
        that the original implementation skipped.

        Implementation note: reuses pgvector's HNSW index via the same
        ``cosine_distance`` operator as ``search``; one extra ANN call per
        candidate fact (≈ 5–10 ms on a few thousand rows). We deliberately
        do NOT use ``search`` because that one returns a list — here we
        only need the single nearest, and the threshold check is much
        easier to reason about as a tight branch.
        """
        if not text or not text.strip():
            return None
        q_vec = await self.embedder.embed(text)
        distance = EpisodicMemoryRow.embedding.cosine_distance(q_vec).label("distance")
        stmt = (
            select(EpisodicMemoryRow, distance)
            .where(
                EpisodicMemoryRow.user_id == self.user_id,
                EpisodicMemoryRow.status == "active",
            )
            .order_by(distance)
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        memory_row, dist = row
        sim = max(0.0, min(1.0, 1.0 - float(dist) / 2.0))
        if sim < threshold:
            return None
        return EpisodicHit(id=memory_row.id, text=memory_row.text, score=sim)

    async def soft_delete(self, mem_id: str, reason: str) -> None:
        _ = reason  # logged via DeprecationRepo
        stmt = (
            update(EpisodicMemoryRow)
            .where(
                EpisodicMemoryRow.user_id == self.user_id,
                EpisodicMemoryRow.id == mem_id,
            )
            .values(status="deprecated")
        )
        await self.session.execute(stmt)

    async def list_recent(self, limit: int = 50) -> list[EpisodicMemory]:
        stmt = (
            select(EpisodicMemoryRow)
            .where(
                EpisodicMemoryRow.user_id == self.user_id,
                EpisodicMemoryRow.status == "active",
            )
            .order_by(EpisodicMemoryRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            EpisodicMemory(
                schema_version=r.schema_version,
                id=r.id,
                user_id=r.user_id,
                text=r.text,
                status=r.status,  # type: ignore[arg-type]
                source_message_id=r.source_message_id,
                source=r.source,
                created_at=r.created_at,
            )
            for r in rows
        ]
