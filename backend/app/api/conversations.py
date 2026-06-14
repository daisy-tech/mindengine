"""Conversation list / detail / messages endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUserId, SessionDep
from app.infra.repositories import ConversationRepo, MessageRepo
from app.services.prompt_archive import PromptArchive

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


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: str,
    user_id: CurrentUserId,
    session: SessionDep,
) -> None:
    """Soft-delete a conversation.

    We deliberately keep the underlying messages — auditability beats
    saved disk space. Listing endpoints filter by ``archived=False``.
    """
    repo = ConversationRepo(session=session, user_id=user_id)
    archived = await repo.archive(conversation_id)
    if not archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    await session.commit()


class AuditMessageDTO(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    meta: dict | None = None
    error: str | None = None


class AuditExportDTO(BaseModel):
    """Full export of a conversation for eval / debugging.

    Includes archived rows (so a deleted conversation can still be
    inspected) and the full ``meta_json`` for every assistant turn —
    which is the basis for the eval harness in M5.
    """

    conversation_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    archived: bool
    messages: list[AuditMessageDTO]


class PromptArchiveDTO(BaseModel):
    """完整 prompt 归档,供「prompt 评估 / 调优」UI 使用。

    与 ``messages.meta_json`` 互补:meta_json 只放 ``system_excerpt``
    (≤500 字)避免表膨胀,这里放完整文本 + 完整 ``llm_messages`` 列表。
    """

    schema: str
    user_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    composed_at: str
    system: str
    user_message: str
    llm_messages: list[dict[str, Any]]
    assistant_reply: str
    meta: dict[str, Any]


@router.get(
    "/{conversation_id}/messages/{message_id}/prompt",
    response_model=PromptArchiveDTO,
)
async def get_message_prompt(
    conversation_id: str,
    message_id: str,
    request: Request,
    user_id: CurrentUserId,
    session: SessionDep,
) -> PromptArchiveDTO:
    """Return the full system + user + assistant text for one assistant turn.

    Source-of-truth is the ``prompt_archive`` (旁路 JSON),not the DB.
    Three failure modes:
    - 503: 服务端没配 archive(``prompt_archive_dir=""``)。前端应禁用按钮。
    - 404 (conv): user_id 跟 conversation_id 不匹配 / 会话不存在。
      返回 404 而不是 403 是为了不泄露「该会话存不存在」(D6)。
    - 404 (entry): 会话存在,但这条 ``message_id`` 没归档(老消息 / 归档
      落盘时 IO 失败 / 不是 assistant 消息)。前端给一个友好提示。
    """
    archive: PromptArchive | None = getattr(
        request.app.state, "prompt_archive", None
    )
    if archive is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "prompt archive is not configured "
                "(set PROMPT_ARCHIVE_DIR or `prompt_archive_dir` in settings)"
            ),
        )

    conv_repo = ConversationRepo(session=session, user_id=user_id)
    if await conv_repo.get(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    try:
        payload = await archive.load(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    except ValueError as exc:
        # 不安全的 user/conv/msg id 字符 —— 只可能来自路径注入尝试。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No archived prompt for this message — it may predate the "
                "archive feature, the assistant message is partial, or the "
                "archive write failed at the time."
            ),
        )
    return PromptArchiveDTO(**payload)


@router.get(
    "/{conversation_id}/audit",
    response_model=AuditExportDTO,
)
async def export_audit(
    conversation_id: str,
    user_id: CurrentUserId,
    session: SessionDep,
    limit: int = 500,
) -> AuditExportDTO:
    if limit <= 0 or limit > 5000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit out of range",
        )
    # Direct table peek so archived conversations are exportable.
    from app.infra.db.models import Conversation as ConvModel

    row = await session.get(ConvModel, conversation_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    msg_repo = MessageRepo(session=session, user_id=user_id)
    msgs = await msg_repo.list_history(conversation_id, limit=limit)
    return AuditExportDTO(
        conversation_id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived=row.archived,
        messages=[
            AuditMessageDTO(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                meta=m.meta_json,
                error=getattr(m, "error", None),
            )
            for m in msgs
        ],
    )
