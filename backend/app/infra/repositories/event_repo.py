"""Event repository (`events` table)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import Event
from app.infra.db.models import EventRow


@dataclass
class EventRepo:
    session: AsyncSession
    user_id: str

    async def list_recent(self, limit: int = 10) -> list[Event]:
        stmt = (
            select(EventRow)
            .where(EventRow.user_id == self.user_id, EventRow.status == "active")
            .order_by(EventRow.occurred_at.desc().nullslast(), EventRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_event(r) for r in rows]

    async def list_by_type(self, type_: str, limit: int = 10) -> list[Event]:
        stmt = (
            select(EventRow)
            .where(
                EventRow.user_id == self.user_id,
                EventRow.type == type_,
                EventRow.status == "active",
            )
            .order_by(EventRow.occurred_at.desc().nullslast(), EventRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_event(r) for r in rows]

    async def insert(self, event: Event) -> None:
        if event.user_id != self.user_id:
            raise ValueError("EventRepo refuses cross-user insert (D6)")
        self.session.add(
            EventRow(
                id=event.id,
                user_id=event.user_id,
                type=event.type,
                title=event.title,
                content=event.content,
                occurred_at=event.occurred_at,
                status=event.status,
                source_message_id=event.source_message_id,
                schema_version=event.schema_version,
                created_at=event.created_at or datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def find_similar_recent(
        self,
        *,
        type_: str,
        title: str,
        within_days: int = 30,
    ) -> Event | None:
        """Return an existing **active** event that is "the same" as the
        given (type, title) within the last ``within_days``, else ``None``.

        Events have no embeddings (doc 08 §3 has no embedding column on
        events_table — adding one is a migration we'd rather defer), so
        we use a string-only heuristic that catches the actual duplicate
        modes seen in the wild:

        - "宅家学习AI" vs "宅家学习AI." (trailing punctuation)
        - "最近心情烦躁" vs "最近感到烦躁" (synonymous open-class words …
           NOT detected; fall back to LLM not re-emitting same fact)
        - "公司因不景气裁员" repeated within minutes

        Strategy: normalize the candidate title (strip whitespace +
        trailing CJK / ASCII punctuation), then look for any active
        same-type event in the window whose normalized title is equal,
        or whose normalized title is a substring of the candidate (or
        vice versa) when the shared overlap is ≥ 4 chars. Substring
        matching catches "宅家学习AI" vs "宅家学习AI 顺便陪猫".
        """
        normalized = _normalize_title(title)
        if not normalized:
            return None
        cutoff = datetime.now(UTC) - timedelta(days=max(0, within_days))
        stmt = (
            select(EventRow)
            .where(
                EventRow.user_id == self.user_id,
                EventRow.type == type_,
                EventRow.status == "active",
                EventRow.created_at >= cutoff,
            )
            .order_by(EventRow.created_at.desc())
            .limit(50)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        for row in rows:
            existing = _normalize_title(row.title or "")
            if not existing:
                continue
            if existing == normalized:
                return _to_event(row)
            # 子串包含算同一件事(短的是长的子串,且 ≥4 个字符 / 字)
            shorter, longer = (
                (existing, normalized)
                if len(existing) <= len(normalized)
                else (normalized, existing)
            )
            if len(shorter) >= 4 and shorter in longer:
                return _to_event(row)
        return None

    async def deprecate(self, event_id: str, reason: str) -> None:
        # NOTE: ``reason`` is logged in memory_deprecations; here we only flip status.
        _ = reason  # surfaced via deprecations table — see DeprecationRepo.insert
        stmt = (
            update(EventRow)
            .where(EventRow.user_id == self.user_id, EventRow.id == event_id)
            .values(status="deprecated")
        )
        await self.session.execute(stmt)


# 用一个 character class(中英文标点)+ 头尾空白来归一化标题。
# 不做语义归一(同义词替换),那要靠 embedding;事件层做不起来。
_TITLE_PUNCT_RE = re.compile(
    r"^[\s\u3000]+|[\s\u3000]+$"
    r"|[。.,，、!！?？:：;；…·\-—()（）【】\[\]\"'\"\u201c\u201d]+$"
)


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    s = title.strip()
    # 反复剥离结尾标点(可能多层:"!!" / "..."),最多 8 层防 DoS
    for _ in range(8):
        new = _TITLE_PUNCT_RE.sub("", s)
        if new == s:
            break
        s = new
    return s.strip().lower()


def _to_event(row: EventRow) -> Event:
    return Event(
        schema_version=row.schema_version,
        id=row.id,
        user_id=row.user_id,
        type=row.type,  # type: ignore[arg-type]
        title=row.title,
        content=row.content,
        occurred_at=row.occurred_at,
        status=row.status,  # type: ignore[arg-type]
        source_message_id=row.source_message_id,
        created_at=row.created_at,
    )
