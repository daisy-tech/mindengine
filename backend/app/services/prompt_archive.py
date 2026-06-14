"""Prompt archive — 旁路落盘完整的 (system, user, assistant) 三元组,
专供「prompt 评估 / 迭代」场景使用。

# 为什么不存到 messages.meta_json

PromptMeta 只保留 `system_excerpt`(≤500 字),目的是不让 messages 表
膨胀(doc 02 §3.4 / lesson 3.4)。但用户做 prompt 调优时需要逐条看到
完整的 system + user + assistant,落库代价太高(每轮 system 会有 4~10KB,
百万轮次就是 GB 级)。

# 折中:旁路 JSON

每轮落到独立文件 ``{root}/{user_id}/{conv_id}/{msg_id}.json``。需要看时
按需读。优点:
- 表结构不变,审计/重放路径不受干扰
- 文件目录天然按 user/conv 分,删某个 user 直接 ``rm -rf``
- NFS 上 read 慢不影响主聊天链路 — 写盘是异步 fire-and-forget
- 单文件命名按 ``message_id``,前端「点该消息」即可定位

# 路径安全

``user_id`` / ``conversation_id`` / ``message_id`` 三段都过
``[A-Za-z0-9_-]+`` 正则,任何其它字符直接 ``ValueError`` —— 同 chat-review
那条路的写法 (lesson 4.1)。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe(value: str, *, label: str) -> str:
    if not value or not _SAFE_SEGMENT.match(value):
        raise ValueError(f"{label!r} contains unsafe characters: {value!r}")
    return value


@runtime_checkable
class PromptArchive(Protocol):
    """The protocol orchestrator depends on.

    Both ``save`` and ``load`` must NEVER raise on the chat hot path —
    a missing archive entry, full disk, etc., must not break the user's
    conversation. Implementations should swallow + log instead.
    """

    async def save(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        payload: dict[str, Any],
    ) -> None: ...

    async def load(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
    ) -> dict[str, Any] | None: ...


@dataclass
class FilesystemPromptArchive:
    """Reference implementation: one JSON file per assistant message."""

    root: Path

    def _resolve(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
    ) -> Path:
        u = _safe(user_id, label="user_id")
        c = _safe(conversation_id, label="conversation_id")
        m = _safe(message_id, label="message_id")
        base = self.root.resolve()
        target = (base / u / c / f"{m}.json").resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:  # pragma: no cover — defense in depth
            raise ValueError(
                f"resolved path escaped root: {target} not under {base}"
            ) from exc
        return target

    async def save(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            target = self._resolve(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            target.write_text(text, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(target, 0o644)
        except (OSError, ValueError):
            # Hot path: never raise. Production has structlog wired up
            # one level higher to capture this.
            return

    async def load(
        self,
        *,
        user_id: str,
        conversation_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        try:
            target = self._resolve(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
        except ValueError:
            return None
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def build_payload(
    *,
    user_id: str,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    user_message: str,
    assistant_reply: str,
    system_text: str,
    llm_messages: list[dict[str, str]],
    meta_json: dict[str, Any],
    composed_at: str,
) -> dict[str, Any]:
    """Assemble the JSON we write per assistant turn.

    Kept as a free function (not a method) so orchestrator can build
    the dict without coupling to a concrete archive type — easier to
    reuse if we later add a SQLite / S3 backend.
    """
    return {
        "schema": "prompt_archive_v1",
        "user_id": user_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "composed_at": composed_at,
        # The fully assembled system prompt (full text, NOT excerpt) —
        # this is the headline reason this archive exists.
        "system": system_text,
        # The exact list of {role, content} dicts we sent to the LLM as
        # `messages` (after history truncation, with the current user
        # turn appended). Lets prompt-eval reproduce token counts +
        # turn-by-turn drift checks.
        "user_message": user_message,
        "llm_messages": llm_messages,
        "assistant_reply": assistant_reply,
        # Same shape as messages.meta_json — copy by value so the file
        # is self-contained even if the DB row is later edited.
        "meta": meta_json,
    }
