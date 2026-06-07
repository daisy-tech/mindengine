"""Banned-entity extraction + cleaning.

Per docs/rebuild/06-Subsystem-Correction.md §5 + §6.4.

Two responsibilities:
- ``BannedExtractor.extract``: given the user's correction utterance and
  the resolved correction targets, ask the LLM for a *short list* of
  entity strings that should be permanently filtered from this user's
  memory ("我老家不是岳西，是怀宁" → ban ``岳西``).
- ``clean_banned_entity``: pure function that enforces the §6.4 rules
  (strip / length 1..MAX / character set / dedup). Used in tests + the
  applier to second-guess what the LLM emitted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.domain.correction import CorrectionTarget
from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

BANNED_EXTRACTOR_SYSTEM = """你在帮助筛选用户**永久禁止**出现在记忆里的实体词。

输入：
- 用户纠正语（含被否定的实体）
- 已解析的目标列表（ref / verb / correct）

任务：抽出**应当永久封禁**的实体词。
要求：
- 每个 entity ≤ 8 字，去空白
- 仅保留中文、英文字母、数字（不要标点/语气词）
- 不要把"correct"那个正确版本写进 banned
- 同一实体只出现一次
- 不确定的就不要列

输出 JSON：
{ "entities": ["岳西", ...] }

仅输出 JSON。"""


_ALLOWED_CHARS = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")


def clean_banned_entity(value: str, *, max_len: int = 8) -> str | None:
    """Apply doc §6.4 cleaning. Returns None if the value should be dropped."""

    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    matches = _ALLOWED_CHARS.findall(stripped)
    if not matches:
        return None
    cleaned = "".join(matches)
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        return None
    return cleaned


def dedup_banned(entities: Iterable[str], *, max_len: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in entities:
        if not isinstance(raw, str):
            continue
        cleaned = clean_banned_entity(raw, max_len=max_len)
        if cleaned is None:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


class BannedEntityResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entities: list[str] = Field(default_factory=list)


@dataclass
class BannedExtractor:
    """LLM-driven extraction of which entities to ban for this user.

    The extractor is wrapped: even when the LLM throws or returns junk
    we always return a (possibly empty) list of cleaned strings — we
    never fail the parent worker because of this step.
    """

    llm: LLMClient
    max_len: int = 8
    max_entities: int = 6

    async def extract(
        self,
        *,
        user_correction: str,
        targets: list[CorrectionTarget],
    ) -> list[str]:
        if not user_correction.strip() or not targets:
            return []

        target_lines = "\n".join(
            f"- ref={t.ref!r} verb={t.verb!r} correct={t.correct!r}"
            for t in targets
        )
        ctx = [
            {
                "role": "user",
                "content": (
                    "用户纠正语：" + user_correction.strip()
                    + "\n目标列表：\n" + target_lines
                ),
            }
        ]
        try:
            raw = await self.llm.complete_json(
                BANNED_EXTRACTOR_SYSTEM,
                ctx,
                schema=BannedEntityResult,
                temperature=0.0,
            )
        except LLMError:
            return self._fallback(targets)
        except Exception:
            return self._fallback(targets)
        if not isinstance(raw, BannedEntityResult):
            return self._fallback(targets)

        # Belt-and-braces: filter LLM output through clean_banned_entity
        # again to enforce §6.4 even if the model misbehaved.
        forbidden_corrects = {
            (t.correct or "").strip().lower()
            for t in targets
            if t.correct
        }
        cleaned: list[str] = []
        for ent in dedup_banned(raw.entities, max_len=self.max_len):
            if ent.lower() in forbidden_corrects:
                continue
            cleaned.append(ent)
            if len(cleaned) >= self.max_entities:
                break

        return cleaned or self._fallback(targets)

    def _fallback(self, targets: list[CorrectionTarget]) -> list[str]:
        """When the LLM is down / unparseable, fall back to ``target.ref``.

        Better to over-ban a literal denied entity than to under-ban and
        let it leak back via extract_memory_task.
        """
        forbidden_corrects = {
            (t.correct or "").strip().lower()
            for t in targets
            if t.correct
        }
        candidates = [t.ref for t in targets]
        cleaned = []
        for ent in dedup_banned(candidates, max_len=self.max_len):
            if ent.lower() in forbidden_corrects:
                continue
            cleaned.append(ent)
            if len(cleaned) >= self.max_entities:
                break
        return cleaned
