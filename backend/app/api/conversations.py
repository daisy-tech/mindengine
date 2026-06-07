"""Conversation list / detail / messages endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUserId, SessionDep
from app.infra.repositories import ConversationRepo, MessageRepo

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationDTO(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageDTO(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    meta: dict | None = None


class CreateConversationRequest(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)


@router.get("", response_model=list[ConversationDTO])
async def list_conversations(
    user_id: CurrentUserId,
    session: SessionDep,
) -> list[ConversationDTO]:
    repo = ConversationRepo(session=session, user_id=user_id)
    items = await repo.list_recent(limit=50)
    return [
        ConversationDTO(
            id=i.id, title=i.title, created_at=i.created_at, updated_at=i.updated_at
        )
        for i in items
    ]


@router.post("", response_model=ConversationDTO, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    user_id: CurrentUserId,
    session: SessionDep,
) -> ConversationDTO:
    conv_id = body.id or f"c_{uuid.uuid4().hex[:24]}"
    repo = ConversationRepo(session=session, user_id=user_id)
    if await repo.get(conv_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conversation {conv_id!r} already exists",
        )
    await repo.create(conv_id, title=body.title)
    fresh = await repo.get(conv_id)
    if fresh is None:  # pragma: no cover — only on race
        raise HTTPException(status_code=500, detail="Failed to create conversation")
    return ConversationDTO(
        id=fresh.id,
        title=fresh.title,
        created_at=fresh.created_at,
        updated_at=fresh.updated_at,
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageDTO])
async def list_messages(
    conversation_id: str,
    user_id: CurrentUserId,
    session: SessionDep,
    limit: int = 50,
) -> list[MessageDTO]:
    conv_repo = ConversationRepo(session=session, user_id=user_id)
    if await conv_repo.get(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    repo = MessageRepo(session=session, user_id=user_id)
    rows = await repo.list_history(conversation_id, limit=limit)
    return [
        MessageDTO(
            id=r.id,
            role=r.role,
            content=r.content,
            created_at=r.created_at,
            meta=r.meta_json,
        )
        for r in rows
    ]
