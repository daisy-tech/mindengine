"""ChatOrchestrator tests using fakes — exercises D2 (decoupled persistence)."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.llm import LLMRole
from app.domain.route import Personality
from app.infra.llm.exceptions import LLMRateLimitError
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.services.chat import ChatOrchestrator, ChatRepos
from app.services.contract_guard import ContractGuard
from app.services.memory_context import MemoryContextLoader
from app.services.memory_router import (
    InMemoryIntentCache,
    LLMIntentClassifier,
    MemoryRouter,
)
from app.services.prompt_composer import PromptComposer
from tests.unit.services._fakes import (
    FakeBannedEntityRepo,
    FakeConversationRepo,
    FakeDeprecationRepo,
    FakeDispatcher,
    FakeEpisodicRepo,
    FakeEventRepo,
    FakeMessageRepo,
    FakeProfileRepo,
    FakeRelationshipRepo,
)


def _wire(
    uid: str,
    *,
    chat_turn: MockTurn,
    intent_payload: dict | None = None,
) -> tuple[ChatOrchestrator, ChatRepos, dict]:
    intent_llm = MockLLMClient(role=LLMRole.INTENT)
    if intent_payload is not None:
        intent_llm.queue_json(intent_payload)
    chat_llm = MockLLMClient(role=LLMRole.CHAT).queue(chat_turn)

    router = MemoryRouter(
        classifier=LLMIntentClassifier(llm=intent_llm),
        cache=InMemoryIntentCache(),
    )
    loader = MemoryContextLoader(
        profile_repo=FakeProfileRepo(user_id=uid),
        event_repo=FakeEventRepo(user_id=uid),
        episodic_repo=FakeEpisodicRepo(user_id=uid),
        relationship_repo=FakeRelationshipRepo(user_id=uid),
        banned_repo=FakeBannedEntityRepo(user_id=uid),
        deprecation_repo=FakeDeprecationRepo(user_id=uid),
    )
    dispatcher = FakeDispatcher()
    orch = ChatOrchestrator(
        router=router,
        loader=loader,
        composer=PromptComposer(),
        guard=ContractGuard(),
        chat_llm=chat_llm,
        dispatcher=dispatcher,
    )
    repos = ChatRepos(
        conversations=FakeConversationRepo(user_id=uid),
        messages=FakeMessageRepo(user_id=uid),
    )
    state = {"intent_llm": intent_llm, "chat_llm": chat_llm, "dispatcher": dispatcher}
    return orch, repos, state


_INTENT_CASUAL = {"intent": "casual", "confidence": 0.7, "reason": "ok"}


@pytest.mark.asyncio
async def test_happy_path_streams_chunks_and_persists() -> None:
    orch, repos, state = _wire(
        "u1",
        chat_turn=MockTurn(chunks=["你好", "，张三"]),
        intent_payload=_INTENT_CASUAL,
    )

    events = []
    async for evt in orch.stream_chat(
        user_id="u1",
        conversation_id="c1",
        user_message="嗨",
        repos=repos,
        personality=Personality.BALANCED,
    ):
        events.append(evt)

    kinds = [e.kind for e in events]
    assert kinds[0] == "meta"
    assert "delta" in kinds
    # Persisted: 1 user msg + 1 assistant msg
    rows = repos.messages.rows  # type: ignore[attr-defined]
    assert len(rows) == 2
    assert rows[0].role == "user" and rows[0].content == "嗨"
    assert rows[1].role == "assistant" and "你好" in rows[1].content
    # meta_json holds prompt_meta
    assert rows[1].meta_json is not None
    assert "route" in rows[1].meta_json
    # After-chat hook fired
    assert state["dispatcher"].after_chat
    assert state["dispatcher"].after_chat[0]["intent"]


@pytest.mark.asyncio
async def test_introvert_question_stripped_via_guard_emits_final() -> None:
    orch, repos, _ = _wire(
        "u1",
        chat_turn=MockTurn(chunks=["收到。", "要聊聊吗？"]),
        intent_payload=_INTENT_CASUAL,
    )
    events = []
    async for evt in orch.stream_chat(
        user_id="u1",
        conversation_id="c1",
        user_message="嗨",
        repos=repos,
        personality=Personality.INTROVERT,
    ):
        events.append(evt)

    finals = [e for e in events if e.kind == "final"]
    assert len(finals) == 1
    assert "?" not in finals[0].data and "？" not in finals[0].data
    saved = repos.messages.rows[1].content  # type: ignore[attr-defined]
    assert "？" not in saved


@pytest.mark.asyncio
async def test_llm_error_emits_error_event_but_still_persists_user_msg() -> None:
    orch, repos, _ = _wire(
        "u1",
        chat_turn=MockTurn(raises=LLMRateLimitError("429")),
        intent_payload=_INTENT_CASUAL,
    )
    events = []
    async for evt in orch.stream_chat(
        user_id="u1",
        conversation_id="c1",
        user_message="嗨",
        repos=repos,
        personality=Personality.BALANCED,
    ):
        events.append(evt)

    kinds = [e.kind for e in events]
    assert "error" in kinds
    # User message still persisted; assistant message persisted as empty/placeholder.
    rows = repos.messages.rows  # type: ignore[attr-defined]
    assert any(r.role == "user" for r in rows)
    assistant_rows = [r for r in rows if r.role == "assistant"]
    assert assistant_rows
    assert assistant_rows[0].meta_json is not None


@pytest.mark.asyncio
async def test_d2_persists_when_consumer_breaks_mid_stream() -> None:
    """If the consumer stops iterating mid-stream, the assistant message
    must still land in the message repo (D2 / decoupled persistence).
    """
    orch, repos, _ = _wire(
        "u1",
        chat_turn=MockTurn(chunks=["你好", "，张三", "，今天天气不错。"]),
        intent_payload=_INTENT_CASUAL,
    )

    agen = orch.stream_chat(
        user_id="u1",
        conversation_id="c1",
        user_message="嗨",
        repos=repos,
        personality=Personality.BALANCED,
    )
    seen = []
    async for evt in agen:
        seen.append(evt)
        if evt.kind == "delta":
            # Simulate a client disconnect: close the async generator.
            await agen.aclose()
            break

    # Allow background task scheduled via create_task to run.
    await asyncio.sleep(0.05)

    # User message persisted immediately; assistant message persisted via
    # the orchestrator's fire-and-forget background task on cancellation.
    rows = repos.messages.rows  # type: ignore[attr-defined]
    user_rows = [r for r in rows if r.role == "user"]
    assistant_rows = [r for r in rows if r.role == "assistant"]
    assert len(user_rows) == 1
    assert len(assistant_rows) == 1
    assert assistant_rows[0].meta_json and assistant_rows[0].meta_json.get("partial") is True


@pytest.mark.asyncio
async def test_hard_rule_correction_skips_intent_classifier() -> None:
    orch, repos, state = _wire(
        "u1", chat_turn=MockTurn(text="好的，我记下了。")
    )
    events = []
    async for evt in orch.stream_chat(
        user_id="u1",
        conversation_id="c1",
        user_message="不对，我不是岳西人",
        repos=repos,
        personality=Personality.BALANCED,
    ):
        events.append(evt)

    # Intent LLM was never consulted because hard rule fired.
    assert state["intent_llm"].calls == []
    # meta event carries intent=correction.
    meta_evt = next(e for e in events if e.kind == "meta")
    assert "correction" in meta_evt.data


@pytest.mark.asyncio
async def test_creates_conversation_if_missing() -> None:
    orch, repos, _ = _wire(
        "u1",
        chat_turn=MockTurn(text="ok"),
        intent_payload=_INTENT_CASUAL,
    )
    assert await repos.conversations.get("c-fresh") is None
    async for _ in orch.stream_chat(
        user_id="u1",
        conversation_id="c-fresh",
        user_message="嗨",
        repos=repos,
        personality=Personality.BALANCED,
    ):
        pass
    assert await repos.conversations.get("c-fresh") is not None
