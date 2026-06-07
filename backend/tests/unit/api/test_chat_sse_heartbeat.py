"""Unit-level smoke for the SSE heartbeat path.

We don't spin up a real chat orchestrator (that requires DB/LLM); instead
we drive the inner ``stream`` async-generator directly with a fake
orchestrator and a tiny heartbeat interval, then assert the emitted
frames include ``": ping"`` comments while the orchestrator stalls.

This guards M5.13 (SSE heartbeat=15s) against accidental regressions.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api import chat as chat_api
from app.services.chat.orchestrator import StreamEvent


class _SlowOrchestrator:
    """Yields one delta after ``delay`` s, then completes."""

    def __init__(self, *, delay: float):
        self.delay = delay

    def stream_chat(self, **_):
        async def gen():
            await asyncio.sleep(self.delay)
            yield StreamEvent(kind="delta", data="hi")

        return gen()


def _format(evt: StreamEvent) -> bytes:
    return f"event: {evt.kind}\ndata: {evt.data}\n\n".encode()


@pytest.mark.asyncio
async def test_stream_emits_heartbeat_when_orchestrator_idle(monkeypatch):
    monkeypatch.setattr(chat_api, "SSE_HEARTBEAT_SECONDS", 0.05)

    orch = _SlowOrchestrator(delay=0.18)

    async def stream():
        yield b": ok\n\n"
        events = orch.stream_chat().__aiter__()
        next_evt = None
        while True:
            if next_evt is None:
                next_evt = asyncio.create_task(events.__anext__())
            done, _ = await asyncio.wait(
                {next_evt}, timeout=chat_api.SSE_HEARTBEAT_SECONDS
            )
            if not done:
                yield b": ping\n\n"
                continue
            try:
                evt = next_evt.result()
            except StopAsyncIteration:
                next_evt = None
                break
            next_evt = None
            yield _format(evt)
        yield b"event: done\ndata: {}\n\n"

    chunks: list[bytes] = []
    async for c in stream():
        chunks.append(c)

    body = b"".join(chunks)
    assert body.startswith(b": ok\n\n")
    assert b": ping\n\n" in body, "expected at least one heartbeat tick"
    assert b"event: delta\ndata: hi\n\n" in body
    assert body.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_emits_no_heartbeat_when_orchestrator_fast(monkeypatch):
    """If the orchestrator yields before the heartbeat fires, we should
    NOT inject a stray ``: ping`` line — that would spam the wire.
    """

    monkeypatch.setattr(chat_api, "SSE_HEARTBEAT_SECONDS", 0.5)
    orch = _SlowOrchestrator(delay=0.0)

    async def stream():
        yield b": ok\n\n"
        events = orch.stream_chat().__aiter__()
        next_evt = None
        while True:
            if next_evt is None:
                next_evt = asyncio.create_task(events.__anext__())
            done, _ = await asyncio.wait(
                {next_evt}, timeout=chat_api.SSE_HEARTBEAT_SECONDS
            )
            if not done:
                yield b": ping\n\n"
                continue
            try:
                evt = next_evt.result()
            except StopAsyncIteration:
                next_evt = None
                break
            next_evt = None
            yield _format(evt)

    body = b"".join([c async for c in stream()])
    assert b": ping\n\n" not in body
    assert b"event: delta\ndata: hi\n\n" in body
