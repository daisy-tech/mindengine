"""ChatOrchestrator — the single entry point for a user-facing chat turn.

Per docs/rebuild/02-TDD.md §2.1-2.3.

Lifecycle of one turn:
    (a) save user message immediately (so reload-after-disconnect works)
    (b) memory router → memory loader → prompt composer
    (c) stream LLM tokens to caller
    (d) on stream termination (normal OR cancellation/disconnect):
        run contract guard, then persist the assistant message + PromptMeta
        regardless of whether the client is still listening (D2)
    (e) dispatch background tasks (memory extraction etc. — M3)

The orchestrator does NOT depend on FastAPI; it only emits an async
iterator of :class:`StreamEvent` chunks. The api layer adapts those into
SSE frames. This keeps the orchestrator unit-testable without httpx.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.llm import DEFAULT_MODELS, LLMRole
from app.domain.prompt import PromptPack
from app.domain.route import MemoryRoute, Personality
from app.infra.llm.exceptions import LLMError
from app.services.chat.history import history_to_messages
from app.services.contract_guard import ContractGuard
from app.services.memory_context import MemoryContextLoader
from app.services.memory_router import MemoryRouter
from app.services.prompt_composer import PromptComposer
from app.services.protocols import (
    ConversationRepository,
    LLMClient,
    MessageRepository,
    TaskDispatcher,
)

# ─── outward-facing event protocol ───────────────────────────────


@dataclass(frozen=True)
class StreamEvent:
    """One streaming event emitted to the API layer.

    The API layer encodes these as SSE frames (see api.chat).
    """

    kind: Literal["meta", "delta", "final", "error"]
    data: str = ""
    """- meta: JSON of {message_id, conversation_id, intent}
       - delta: a single text chunk
       - final: the post-guard text (if it differs from the streamed text)
       - error: an error message; stream is aborted right after"""


# ─── repo bundle ─────────────────────────────────────────────────


@dataclass
class ChatRepos:
    """All repos the orchestrator needs for one turn. Bundled so the
    api layer doesn't have to thread 8 keyword args."""

    conversations: ConversationRepository
    messages: MessageRepository


# ─── orchestrator ────────────────────────────────────────────────


@dataclass
class ChatOrchestrator:
    router: MemoryRouter
    loader: MemoryContextLoader
    composer: PromptComposer
    guard: ContractGuard
    chat_llm: LLMClient
    dispatcher: TaskDispatcher
    history_window: int = 12
    chat_temperature: float = 0.7
    _bg_tasks: set[asyncio.Task] = field(default_factory=set)

    async def stream_chat(
        self,
        *,
        user_id: str,
        conversation_id: str,
        user_message: str,
        repos: ChatRepos,
        personality: Personality = Personality.BALANCED,
    ) -> AsyncIterator[StreamEvent]:
        """Run a full chat turn, yielding StreamEvents to the caller.

        See module docstring for D2 cancellation semantics.
        Usage:  ``async for evt in orch.stream_chat(...): ...``
        """
        # ─── (a) save user message + ensure conversation exists.
        await self._ensure_conversation(repos.conversations, conversation_id)
        user_msg_id = self._gen_id("u")
        await repos.messages.insert_user(
            conversation_id=conversation_id,
            message_id=user_msg_id,
            content=user_message,
        )

        # ─── (b) router → loader → composer.
        history_dtos = await repos.messages.list_history(conversation_id, self.history_window)
        history = history_to_messages(history_dtos, exclude_message_id=user_msg_id)

        try:
            route = await self.router.route(
                user_id=user_id,
                message=user_message,
                history=history,
                personality=personality,
            )
            ctx = await self.loader.load(route)
            pack = self.composer.compose(
                route=route,
                ctx=ctx,
                chat_model=DEFAULT_MODELS[LLMRole.CHAT],
            )
        except Exception as e:
            yield StreamEvent(kind="error", data=f"compose_failed: {e!r}")
            return

        assistant_msg_id = self._gen_id("a")
        meta_payload = (
            f'{{"message_id":"{assistant_msg_id}",'
            f'"conversation_id":"{conversation_id}",'
            f'"intent":"{route.intent.value}",'
            f'"intent_source":"{route.intent_source.value}"}}'
        )
        yield StreamEvent(kind="meta", data=meta_payload)

        # ─── (c) stream LLM, capturing full text.
        llm_messages = self._build_llm_messages(history, user_message)
        chunks: list[str] = []
        completed = False
        cancelled = False
        llm_error: str | None = None
        try:
            stream = await self.chat_llm.stream(
                pack.system,
                llm_messages,
                temperature=self.chat_temperature,
            )
            async for chunk in stream:
                chunks.append(chunk)
                yield StreamEvent(kind="delta", data=chunk)
            completed = True
        except asyncio.CancelledError:
            # Client disconnected mid-stream. Fall through to finally so
            # we still persist what we have, then re-raise so the parent
            # task knows we were cancelled (D2).
            cancelled = True
            raise
        except LLMError as e:
            llm_error = f"{type(e).__name__}: {e}"
            yield StreamEvent(kind="error", data=llm_error)
        finally:
            await self._persist_and_dispatch(
                pack=pack,
                route=route,
                repos=repos,
                conversation_id=conversation_id,
                user_msg_id=user_msg_id,
                assistant_msg_id=assistant_msg_id,
                user_id=user_id,
                chunks=chunks,
                completed=completed,
                cancelled=cancelled,
                llm_error=llm_error,
            )

        # ─── (d) emit final post-guard text if it changed.
        if completed and chunks:
            raw = "".join(chunks)
            result = self.guard.enforce(raw, personality)
            if result.text and result.text != raw:
                yield StreamEvent(kind="final", data=result.text)

    # ─── helpers ─────────────────────────────────────────────────

    async def _persist_and_dispatch(
        self,
        *,
        pack: PromptPack,
        route: MemoryRoute,
        repos: ChatRepos,
        conversation_id: str,
        user_msg_id: str,
        assistant_msg_id: str,
        user_id: str,
        chunks: list[str],
        completed: bool,
        cancelled: bool,
        llm_error: str | None,
    ) -> None:
        """Run guard + persist + dispatch in a way that tolerates being
        called from a cancelled task (D2).

        - completed: stream finished normally → await inline.
        - llm_error (and not cancelled): we still control the flow → await inline.
        - cancelled: parent task is cancelled → fire-and-forget so the
          assistant message + meta still land (D2).
        """
        raw = "".join(chunks)
        result = self.guard.enforce(raw, route.personality)
        pack.meta.contract_enforced = result.enforcement
        coro = self._do_persist_and_dispatch(
            pack=pack,
            route=route,
            repos=repos,
            conversation_id=conversation_id,
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
            user_id=user_id,
            text=result.text,
            completed=completed,
            llm_error=llm_error,
        )
        if cancelled:
            task = asyncio.create_task(coro)
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        else:
            await coro

    async def _do_persist_and_dispatch(
        self,
        *,
        pack: PromptPack,
        route: MemoryRoute,
        repos: ChatRepos,
        conversation_id: str,
        user_msg_id: str,
        assistant_msg_id: str,
        user_id: str,
        text: str,
        completed: bool,
        llm_error: str | None,
    ) -> None:
        try:
            await repos.messages.insert_assistant(
                conversation_id=conversation_id,
                message_id=assistant_msg_id,
                content=text or "(empty)",
                meta=pack.meta,
                partial=not completed,
                error=llm_error,
            )
            await repos.conversations.touch(conversation_id)
            # M3 will fan out memory-extract tasks here.
            await self.dispatcher.dispatch_after_chat(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=user_msg_id,
                intent=route.intent.value,
            )
        except Exception:
            # Never let persistence failures take down the connection.
            # In production, structlog will pick this up via uncaught logger.
            pass

    @staticmethod
    async def _ensure_conversation(repo: ConversationRepository, conv_id: str) -> None:
        existing = await repo.get(conv_id)
        if existing is None:
            await repo.create(conv_id, title=None)

    def _build_llm_messages(
        self,
        history: Sequence[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]:
        msgs = list(history)
        msgs.append({"role": "user", "content": user_message})
        return msgs

    @staticmethod
    def _gen_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:24]}"


def fresh_message_id(prefix: str = "m") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"
