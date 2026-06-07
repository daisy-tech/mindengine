"""LLM-judged action per correction candidate.

Per docs/rebuild/06-Subsystem-Correction.md §5.

Given (user_correction, ai_previous_reply, candidate.text), the judge
emits one of: ``deprecate`` / ``update`` / ``audit_only`` along with a
confidence in [0, 1] and a short reason. ``new_text`` is required when
action == "update".

We deliberately keep the judge stateless and rely on a configurable
confidence threshold (default 0.7, from ``CORRECTION_CONFIDENCE_THRESHOLD``)
to drop low-confidence verdicts to ``audit_only`` even if the model said
otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.domain.correction import (
    CorrectionAction,
    CorrectionCandidate,
    CorrectionJudgement,
)
from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

CORRECTION_JUDGE_SYSTEM = """你在评估一条"记忆候选"是否需要根据用户纠正被清除。

输入三段：
- 用户纠正语
- AI 上一轮回复
- 候选记忆原文（含来源层）

判断：
1. 这条候选是否就是用户在否定的对象？
2. 行动建议：
   - deprecate: 完全删除（用户明确说不存在/不对）
   - update: 替换为正确版本（用户给出了 new_text）
   - audit_only: 仅审计，不动数据（你不能很确定）
3. 置信度 0-1（严格：50/50 → audit_only）
4. new_text 必须来自用户纠正语，不要自己编

输出 JSON：
{
  "action": "deprecate" | "update" | "audit_only",
  "confidence": 0.x,
  "reason": "≤20 字",
  "new_text": "<update 时必填，否则 null>"
}

仅输出 JSON，不解释。"""


class _RawJudgement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: CorrectionAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    new_text: str | None = None


@dataclass(frozen=True)
class JudgementOutcome:
    """Full record of one (candidate, judgement) decision.

    Carries the original ``CorrectionCandidate`` so the applier can act
    without re-querying the repos.
    """

    candidate: CorrectionCandidate
    judgement: CorrectionJudgement


@dataclass
class CorrectionJudge:
    """Stateless judge — one instance per process is enough.

    ``confidence_threshold`` mirrors the env var
    ``CORRECTION_CONFIDENCE_THRESHOLD`` (doc 06 §10.4). Verdicts below
    threshold get coerced to ``audit_only``.
    """

    llm: LLMClient
    confidence_threshold: float = 0.7
    max_new_text_chars: int = 80

    async def judge(
        self,
        *,
        user_correction: str,
        ai_previous_reply: str,
        candidate: CorrectionCandidate,
    ) -> JudgementOutcome:
        ctx = [
            {
                "role": "user",
                "content": (
                    "用户纠正语：" + user_correction.strip()
                    + "\nAI 上一轮回复：" + (ai_previous_reply or "").strip()
                    + f"\n候选记忆来源：{candidate.source}"
                    + f"\n候选记忆原文：{candidate.text}"
                ),
            }
        ]
        try:
            raw = await self.llm.complete_json(
                CORRECTION_JUDGE_SYSTEM,
                ctx,
                schema=_RawJudgement,
                temperature=0.0,
            )
        except LLMError:
            return self._audit_only(candidate, "llm_error")
        except Exception:
            return self._audit_only(candidate, "llm_error")
        if not isinstance(raw, _RawJudgement):
            return self._audit_only(candidate, "schema_error")

        action = raw.action
        confidence = float(raw.confidence)
        reason = (raw.reason or "")[:60]
        new_text = self._normalize_new_text(raw.new_text)

        if action == "update" and not new_text:
            # Doc §5: update without a usable new_text degrades to deprecate.
            action = "deprecate"
            reason = (reason + " (update_no_new_text)").strip()

        if confidence < self.confidence_threshold:
            action = "audit_only"

        return JudgementOutcome(
            candidate=candidate,
            judgement=CorrectionJudgement(
                action=action,
                confidence=confidence,
                reason=reason,
                new_text=new_text if action == "update" else None,
            ),
        )

    def _audit_only(
        self, candidate: CorrectionCandidate, reason: str
    ) -> JudgementOutcome:
        return JudgementOutcome(
            candidate=candidate,
            judgement=CorrectionJudgement(
                action="audit_only",
                confidence=0.0,
                reason=reason,
            ),
        )

    def _normalize_new_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > self.max_new_text_chars:
            cleaned = cleaned[: self.max_new_text_chars].rstrip()
        return cleaned
