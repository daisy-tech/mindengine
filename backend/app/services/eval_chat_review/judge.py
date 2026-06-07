"""Optional LLM judge for L1 flagged turns (default OFF).

Per docs/rebuild/07-Subsystem-Eval-Lab.md §3.3a (decision D5).

Runs a single cheap LLM call per L1-flagged turn (status fail/suspicious
with severity ≥ medium) to either confirm or down-rate the verdict.

The judge is defensive:
- Returns ``JudgeVerdict(agrees=True, ...)`` and a noop ``corrected_status``
  on any LLM failure (we never *upgrade* a status to bad without a clear
  signal — the worst we'll do is leave the L1 status as-is).
- Bounded by ``max_calls_per_review`` to keep cost predictable.

The reviewer applies verdicts after the rules have run; the judge
itself doesn't mutate ``RuleResult`` objects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.infra.llm.exceptions import LLMError
from app.services.eval_chat_review._types import (
    FAIL,
    HIGH,
    MEDIUM,
    PASS,
    SKIP,
    SUSPICIOUS,
    RuleResult,
    TurnPack,
)
from app.services.protocols import LLMClient

JUDGE_SYSTEM = """你是评测复核员。以下是 L1 启发式规则对一轮对话标红的结果。
请基于用户输入 + AI 回复 + 命中规则，重新判断该轮的最终状态。

候选状态：
- ok: 看起来正常
- suspicious: 偏离指引但不严重
- bad: 明显错误（事实错/复述失败/池外编造/破坏人格契约）

输出 JSON：
{
  "agrees": <bool, 是否同意 L1 标红>,
  "corrected_status": "ok" | "suspicious" | "bad",
  "reason": "<≤30 字>"
}

仅输出 JSON。"""


_TARGETED_STATUSES = (FAIL, SUSPICIOUS)


class _RawVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agrees: bool = True
    corrected_status: Literal["ok", "suspicious", "bad"] = "suspicious"
    reason: str = Field(default="", max_length=120)


@dataclass(frozen=True)
class JudgeVerdict:
    agrees: bool
    corrected_status: str
    reason: str


@dataclass
class L1Judge:
    """Cheap small-model JUDGE; default disabled via settings.

    ``max_calls_per_review`` caps cost — flagged turns past this limit
    get the default agreeable verdict.
    """

    llm: LLMClient
    max_calls_per_review: int = 5

    def is_flagged(self, results: Sequence[RuleResult]) -> bool:
        return any(
            r.status in _TARGETED_STATUSES and r.severity in (HIGH, MEDIUM)
            for r in results
        )

    async def judge_turn(
        self, *, turn: TurnPack, l1_results: Sequence[RuleResult]
    ) -> JudgeVerdict | None:
        if not self.is_flagged(l1_results):
            return None
        flagged = [
            f"{r.id}:{r.status.value}:{r.severity.value}:{r.detail}"
            for r in l1_results
            if r.status in _TARGETED_STATUSES
        ]
        ctx = [
            {
                "role": "user",
                "content": (
                    "用户输入：" + (turn.user_message or "")
                    + "\nAI 回复：" + (turn.assistant_reply or "")
                    + "\nL1 标红："
                    + "\n  - " + "\n  - ".join(flagged)
                ),
            }
        ]
        try:
            raw = await self.llm.complete_json(
                JUDGE_SYSTEM, ctx, schema=_RawVerdict, temperature=0.0
            )
        except LLMError:
            return JudgeVerdict(agrees=True, corrected_status="suspicious", reason="llm_error")
        except Exception:
            return JudgeVerdict(agrees=True, corrected_status="suspicious", reason="llm_error")
        if not isinstance(raw, _RawVerdict):
            return JudgeVerdict(agrees=True, corrected_status="suspicious", reason="schema_error")
        return JudgeVerdict(
            agrees=raw.agrees,
            corrected_status=raw.corrected_status,
            reason=raw.reason,
        )

    @staticmethod
    def apply_to_status(verdict: JudgeVerdict | None, current_final: str) -> str:
        """Apply doc §3.3a semantics:
        - L1 suspicious + judge says ok → final 'ok' (downgrade)
        - L1 fail + judge says ok → final 'ok' (downgrade)
        - L1 suspicious + judge says bad → final 'bad' (upgrade)
        - judge missing → unchanged
        """
        if verdict is None:
            return current_final
        if verdict.corrected_status == "bad":
            return "bad"
        if verdict.corrected_status == "ok" and current_final in (
            "suspicious",
            "bad",
        ):
            return "ok"
        return current_final


# Re-export the semantic-only enums so call sites don't have to import _types.
__all__ = [
    "FAIL",
    "JUDGE_SYSTEM",
    "JudgeVerdict",
    "L1Judge",
    "PASS",
    "SKIP",
    "SUSPICIOUS",
]
