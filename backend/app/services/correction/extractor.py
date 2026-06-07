"""LLM-driven correction-target extraction.

Per docs/rebuild/06-Subsystem-Correction.md §4 Step 1.

Input: the user's correction utterance + the assistant's previous reply
that triggered it. Output: a list of ``CorrectionTarget`` (ref / verb /
correct?) plus, optionally, the AI's pre-correction context.

Failures (LLM error / empty payload) collapse to "no targets" so the
worker silently no-ops rather than crashing the queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.correction import CorrectionTarget
from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

CORRECTION_EXTRACTOR_SYSTEM = """你是用户纠错语解析器。
任务：从用户最近一条"纠正"语义的话中抽出**被否定的实体**和（如有）**正确版本**。

输入是两段话：
- AI 上一轮回复（可能含错误事实）
- 用户的纠错语

每个目标：
{
  "ref": "被否定的具体实体词（人名/地名/事实碎片，≤16字）",
  "verb": "用户用来否定的动词，如 不是 / 不对 / 错了 / 没有",
  "correct": "用户给出的正确版本（如有），≤16字；没给就 null"
}

输出 JSON：
{ "targets": [...] }

严格要求：
- 一句话里可能否定多个实体，分别列出。
- 模糊否定（如"算了不说了"）不算纠错，输出 []。
- ref / correct 必须是用户**字面**说过的片段，不要泛化或重写。
- 仅输出 JSON。"""


class _RawTarget(BaseModel):
    """Raw shape parsed from the LLM. Cleaned/validated below."""

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(min_length=1, max_length=24)
    verb: str = Field(min_length=1, max_length=8)
    correct: str | None = Field(default=None, max_length=24)

    @field_validator("ref", "verb")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("correct")
    @classmethod
    def _normalize_correct(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


class CorrectionExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    targets: list[_RawTarget] = Field(default_factory=list)


@dataclass
class CorrectionTargetExtractor:
    """Extracts correction targets from one (user_msg, ai_msg) pair."""

    llm: LLMClient
    max_targets: int = 4

    async def extract(
        self,
        *,
        user_correction: str,
        ai_previous_reply: str,
    ) -> list[CorrectionTarget]:
        cleaned_user = (user_correction or "").strip()
        if not cleaned_user:
            return []

        ctx = [
            {"role": "assistant", "content": (ai_previous_reply or "").strip()},
            {"role": "user", "content": cleaned_user},
        ]
        try:
            result = await self.llm.complete_json(
                CORRECTION_EXTRACTOR_SYSTEM,
                ctx,
                schema=CorrectionExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return []
        except Exception:
            return []
        if not isinstance(result, CorrectionExtractionResult):
            return []

        out: list[CorrectionTarget] = []
        seen: set[str] = set()
        for raw in result.targets:
            ref = raw.ref.strip()
            if not ref:
                continue
            key = ref.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                CorrectionTarget(
                    ref=ref,
                    verb=raw.verb.strip() or "否定",
                    correct=raw.correct,
                )
            )
            if len(out) >= self.max_targets:
                break
        return out
