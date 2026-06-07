"""Service-layer Protocols (interfaces).

Per docs/rebuild/02-TDD.md §1.2 + 05-Subsystem-Memory-Layers.md §8.1:
all repositories, LLM clients, and dispatcher are defined as
runtime_checkable Protocols here so the service layer can be tested
in-memory without any IO.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.domain.correction import (
    BannedEntity,
    CorrectionJudgement,
    CorrectionTarget,
    MemoryDeprecation,
)
from app.domain.llm import LLMRole
from app.domain.memory import (
    EpisodicHit,
    EpisodicMemory,
    Event,
    Profile,
    Relationship,
)
from app.domain.prompt import PromptMeta
from app.domain.route import ClassifyResult, MemoryRoute

# ─────────────────────────────────────────────────────── LLM ──


@runtime_checkable
class LLMClient(Protocol):
    """OpenAI-compatible client abstraction (doc 09 §1.3).

    Implementations: infra.llm.qwen_client.QwenClient, infra.llm.mock_client.MockLLMClient.
    """

    role: LLMRole
    model: str

    async def complete(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str: ...

    async def stream(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...

    async def complete_json(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        schema: type[Any],
        temperature: float = 0.0,
    ) -> Any: ...


@runtime_checkable
class EmbeddingClient(Protocol):
    """Used by infra.vector to compute pgvector embeddings."""

    model: str
    dim: int

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class IntentClassifier(Protocol):
    """Layer-2 small-model classifier (doc 03 §4)."""

    async def classify(
        self,
        message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> ClassifyResult: ...


# ─────────────────────────────────────────────────────── Repositories ──


@runtime_checkable
class ProfileRepository(Protocol):
    user_id: str

    async def get(self) -> Profile | None: ...
    async def upsert(self, profile: Profile) -> None: ...
    async def merge(self, partial: dict[str, Any]) -> Profile: ...


@runtime_checkable
class EventRepository(Protocol):
    user_id: str

    async def list_recent(self, limit: int = 10) -> list[Event]: ...
    async def list_by_type(self, type_: str, limit: int = 10) -> list[Event]: ...
    async def insert(self, event: Event) -> None: ...
    async def deprecate(self, event_id: str, reason: str) -> None: ...


@runtime_checkable
class EpisodicRepository(Protocol):
    user_id: str

    async def add(
        self,
        text: str,
        metadata: dict[str, Any],
    ) -> str: ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[EpisodicHit]: ...

    async def soft_delete(self, mem_id: str, reason: str) -> None: ...

    async def list_recent(self, limit: int = 50) -> list[EpisodicMemory]: ...


@runtime_checkable
class RelationshipRepository(Protocol):
    user_id: str

    async def list_for_intent(self, route: MemoryRoute) -> list[Relationship]: ...
    async def find_by_name(self, name: str) -> list[Relationship]: ...
    async def upsert(self, rel: Relationship) -> None: ...


@runtime_checkable
class BannedEntityRepository(Protocol):
    user_id: str

    async def list(self) -> list[BannedEntity]: ...
    async def add_many(self, entities: Sequence[BannedEntity]) -> int: ...


@runtime_checkable
class DeprecationRepository(Protocol):
    user_id: str

    async def list_episodic_ids(self) -> set[str]: ...
    async def list_recent(self, limit: int = 50) -> list[MemoryDeprecation]: ...
    async def insert(self, dep: MemoryDeprecation) -> None: ...


# ─────────────────────────────────────────────────────── conversations ──


@runtime_checkable
class ConversationRepository(Protocol):
    user_id: str

    async def list_recent(self, limit: int = 20) -> list[ConversationSummary]: ...
    async def get(self, conversation_id: str) -> ConversationSummary | None: ...
    async def create(self, conversation_id: str, title: str | None = None) -> None: ...
    async def touch(self, conversation_id: str, when: datetime | None = None) -> None: ...


@runtime_checkable
class MessageRepository(Protocol):
    """Reads/writes `messages` table.

    Per docs/rebuild/02-TDD.md §2.3 (D2): assistant-message persistence
    must run independently of the SSE client lifecycle, hence the explicit
    `partial` flag and meta_json carrying PromptMeta.
    """

    user_id: str

    async def list_history(
        self, conversation_id: str, limit: int = 12
    ) -> list[MessageRow]: ...

    async def insert_user(
        self,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> None: ...

    async def insert_assistant(
        self,
        *,
        conversation_id: str,
        message_id: str,
        content: str,
        meta: PromptMeta,
        partial: bool = False,
        error: str | None = None,
    ) -> None: ...


# Lightweight DTOs returned by the conversation/message repos. Kept here
# rather than in domain/ because they're a service<->infra contract that
# pure domain doesn't need to know about.
class ConversationSummary(Protocol):
    id: str
    user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageRow(Protocol):
    id: str
    conversation_id: str
    user_id: str
    role: str
    content: str
    meta_json: dict[str, Any] | None
    created_at: datetime


# ─────────────────────────────────────────────────────── cache ──


@runtime_checkable
class IntentCache(Protocol):
    """Per-user intent-classification result cache (D4 / doc 03 §8).

    Implementations: Redis-backed for prod, in-memory dict for tests.
    Key is computed from (user_id, last few turns + new message); the
    cache is only consulted when there is no clear hard-rule signal.
    """

    async def get(self, user_id: str, key: str) -> ClassifyResult | None: ...

    async def set(
        self,
        user_id: str,
        key: str,
        value: ClassifyResult,
        *,
        ttl_seconds: int = 300,
    ) -> None: ...


# ─────────────────────────────────────────────────────── dispatch ──


@runtime_checkable
class TaskDispatcher(Protocol):
    """Background-task dispatch facade (doc 02 §3.3).

    Concrete impls: a Celery-backed one for production, an in-memory one
    for unit tests.
    """

    async def dispatch_after_chat(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        intent: str,
    ) -> None: ...

    async def dispatch_correction_cleanup(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        targets: Sequence[CorrectionTarget] | None = None,
        judgements: Sequence[CorrectionJudgement] | None = None,
    ) -> None: ...
