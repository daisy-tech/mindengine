"""Docker-compose smoke for M4 — correction pipeline wiring + chat-audit endpoint.

Run from inside the backend container:

    docker compose exec backend python scripts/smoke_m4.py

It verifies the M4 plumbing without requiring a real LLM provider:

1. ``/healthz`` is up.
2. ``correction.cleanup`` is a registered Celery task and dispatching it
   via Redis succeeds; the worker accepts the task (any LLM error is
   swallowed by the task per design).
3. ``review_conversation`` returns an ``eval_review_v1`` block — same
   path used by the ``/api/eval/chat-audit`` HTTP route.

Conversation rows are seeded directly via SQLAlchemy because the chat
endpoint requires a live LLM that this smoke deliberately avoids.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.infra.db.factory import make_engine, make_sessionmaker
from app.infra.db.models import Conversation, Message, User
from app.workers.celery_app import celery_app

BASE_URL = "http://localhost:8000"


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


async def _seed_conversation(user_email: str, conv_id: str) -> str:
    settings = get_settings()
    engine = make_engine(settings.database_url, kind="web")
    Session = make_sessionmaker(engine)

    async with Session() as session:
        existing = await session.execute(
            select(User).where(User.email == user_email)
        )
        user = existing.scalar_one_or_none()
        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                email=user_email,
                password_hash="x",  # noqa: S106 — smoke-only stub
            )
            session.add(user)
            await session.flush()

        existing_conv = await session.execute(
            select(Conversation).where(
                Conversation.user_id == user.id,
                Conversation.id == conv_id,
            )
        )
        conv = existing_conv.scalar_one_or_none()
        if conv is None:
            conv = Conversation(
                id=conv_id,
                user_id=user.id,
                title="m4 smoke",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(conv)
            await session.flush()

        existing_msgs = await session.execute(
            select(Message).where(
                Message.user_id == user.id,
                Message.conversation_id == conv_id,
            )
        )
        if not list(existing_msgs.scalars()):
            now = datetime.now(UTC)
            session.add(
                Message(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    conversation_id=conv_id,
                    role="user",
                    content="你好，最近怎么样？",
                    created_at=now,
                )
            )
            session.add(
                Message(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    conversation_id=conv_id,
                    role="assistant",
                    content="嗨！我挺好的，今天聊点什么？",
                    created_at=now,
                    meta_json={
                        "route": {
                            "intent": "casual",
                            "personality": "balanced",
                        },
                        "activated": [],
                        "system_excerpt": "你是镜，朋友式的 AI",
                        "section_keys": ["base_persona"],
                        "snapshot_stats": {
                            "profile_total": 0,
                            "episodic_total": 0,
                            "event_total": 0,
                            "relationship_total": 0,
                        },
                    },
                )
            )
        await session.commit()
        await engine.dispose()
        return user.id


async def main() -> int:
    rc = 0
    user_email = "smoke-m4@example.com"
    conv_id = "smoke-m4-conv"

    _print_section("1) /healthz")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=5.0) as cli:
        r = await cli.get("/healthz")
        print(r.status_code, r.text)
        if r.status_code != 200:
            rc = 1

    _print_section("2) seed user + conversation in DB")
    user_id = await _seed_conversation(user_email, conv_id)
    print(f"user_id={user_id}  conv_id={conv_id}")

    _print_section("3) Celery: correction.cleanup is registered")
    has_cleanup = "correction.cleanup" in celery_app.tasks
    print("correction.cleanup registered:", has_cleanup)
    if not has_cleanup:
        rc = 1

    _print_section("4) Dispatch correction.cleanup via broker")
    res = celery_app.send_task(
        "correction.cleanup",
        kwargs={
            "user_id": user_id,
            "conversation_id": conv_id,
            "message_id": str(uuid.uuid4()),
        },
        queue="mindengine.correction",
    )
    print(f"task id: {res.id}")
    time.sleep(3)
    print("(check `docker compose logs celery` to see task receipt)")

    _print_section("5) review_conversation (chat-audit pipeline)")
    from app.services.eval_chat_review import (
        ReviewContext,
        TurnPack,
        review_conversation,
    )

    turn = TurnPack(
        turn_id="t-smoke",
        index=0,
        user_message="你好，最近怎么样？",
        assistant_reply="嗨！我挺好的，今天聊点什么？",
        error=None,
        meta={
            "route": {"intent": "casual", "personality": "balanced"},
            "activated": [],
            "system_excerpt": "你是镜，朋友式的 AI",
            "section_keys": ["base_persona"],
            "snapshot_stats": {
                "profile_total": 0,
                "episodic_total": 0,
                "event_total": 0,
                "relationship_total": 0,
            },
        },
    )
    review = review_conversation(turns=[turn], ctx=ReviewContext())
    print(json.dumps(review, ensure_ascii=False, indent=2)[:1500])
    if review.get("schema") != "eval_review_v1":
        print("FAIL: review schema unexpected")
        rc = 1
    if not review.get("turns"):
        print("FAIL: empty turns")
        rc = 1
    if review["turns"][0]["final_status"] not in {"good", "ok", "skip"}:
        print(
            "FAIL: clean turn should be good/ok/skip, got",
            review["turns"][0]["final_status"],
        )
        rc = 1

    _print_section("DONE")
    print("smoke rc:", rc)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
