"""Chat endpoint — POST a user message, get an SSE stream back.

Per docs/rebuild/02-TDD.md §2.1.

The HTTP path uses POST + SSE (rather than GET + SSE) so the browser can
send a JSON body. We respond with the SSE media type and stream events
formatted as per :class:`StreamEvent`.

Per D2: persistence runs through ``ChatOrchestrator`` and is decoupled
from this connection; even if the client drops mid-stream, the assistant
message is saved.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentUserId,
    SessionDep,
    SettingsDep,
    build_chat_orchestrator_factory,
)
from app.domain.route import Personality
from app.infra.repositories import ConversationRepo, MessageRepo
from app.services.chat import ChatRepos, StreamEvent

# How often to emit an SSE comment heartbeat so intermediaries (nginx,
# CloudFront, mobile NAT) don't reap an idle connection. Per
# docs/rebuild/11-Roadmap.md M5 §3 — 15s is the safe upper bound for a
# 60s proxy idle timeout.
SSE_HEARTBEAT_SECONDS = 15.0

ChatOrchFactory = Annotated[
    "object", Depends(build_chat_orchestrator_factory)
]

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=8000)
    personality: Personality | None = None


def _format_sse(event: StreamEvent) -> bytes:
    return f"event: {event.kind}\ndata: {event.data}\n\n".encode()


@router.post("", response_class=StreamingResponse)
async def chat(
    body: ChatRequest,
    user_id: CurrentUserId,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    factory: ChatOrchFactory,
) -> StreamingResponse:
    repos = ChatRepos(
        conversations=ConversationRepo(session=session, user_id=user_id),
        messages=MessageRepo(session=session, user_id=user_id),
    )
    personality = body.personality or Personality.BALANCED
    orchestrator = factory(
        session=session,
        user_id=user_id,
        embedder=request.app.state.embedder,
    )

    async def stream() -> AsyncIterator[bytes]:
        # Initial SSE comment to flush headers / let proxies establish the connection.
        yield b": ok\n\n"

        # Race the orchestrator events against a periodic heartbeat tick.
        # The heartbeat is just an SSE comment (";:" prefix) so the
        # browser parser ignores it but proxies see traffic and don't
        # close the connection.
        events = orchestrator.stream_chat(
            user_id=user_id,
            conversation_id=body.conversation_id,
            user_message=body.message,
            repos=repos,
            personality=personality,
        ).__aiter__()

        next_evt: asyncio.Task[Any] | None = None
        while True:
            if next_evt is None:
                next_evt = asyncio.create_task(events.__anext__())
            try:
                done, _ = await asyncio.wait(
                    {next_evt}, timeout=SSE_HEARTBEAT_SECONDS
                )
            except asyncio.CancelledError:
                next_evt.cancel()
                raise
            if not done:
                # Heartbeat tick — keep the line alive.
                yield b": ping\n\n"
                continue
            try:
                event = next_evt.result()
            except StopAsyncIteration:
                next_evt = None
                break
            next_evt = None
            yield _format_sse(event)

        yield b"event: done\ndata: {}\n\n"

    headers: dict[str, Any] = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Disable proxy buffering so chunks reach the client promptly.
        "X-Accel-Buffering": "no",
    }
    _ = settings  # reserved for rate-limiting / dev-mode flags
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@router.post("/_decode_meta", include_in_schema=False)
async def _decode_meta_dev(
    _user_id: CurrentUserId,
    raw_meta: dict[str, Any],
) -> dict[str, Any]:
    """Round-trips a meta_json blob to confirm schema compatibility.

    Strictly a dev/debug helper — no behaviour change.
    """
    # Validate shape by re-parsing
    return json.loads(json.dumps(raw_meta))
