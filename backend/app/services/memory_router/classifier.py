"""Layer 2 of the router: small-model intent classifier.

Per docs/rebuild/03-Subsystem-Memory-Router.md §4 + 09-LLM-Strategy.md §1.3.

We pass at most the last 2 user+assistant turns (D8) plus the new user
message, and ask the model to return a strict JSON object with one of the
nine intents and a confidence in [0, 1].

Bad output is NOT fatal — caller (MemoryRouter) falls back to CASUAL with
``IntentSource.FALLBACK`` and a low confidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.route import ClassifyResult, Intent
from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

INTENT_CLASSIFIER_SYSTEM = """你是一个意图分类器。给定用户的最新一句话和最近 2 轮对话，输出严格 JSON：
{"intent": "<intent_name>", "confidence": <0..1>, "reason": "<≤30字解释>"}

intent 必须是以下九个之一（其它一律选 casual）：
- casual               日常聊天，无明确诉求
- self_summary         请助手总结/概括用户自己
- memory_challenge     询问助手记不记得某件事
- relationship_topic   讨论家人、伴侣、朋友等关系
- emotional_support    倾诉负面情绪、寻求安慰
- plan_followup        谈论之前的计划/约定/打算
- preference_request   询问助手偏好/建议
- correction           更正助手过去说错的话
- knowledge_task       请助手解答客观问题/做事

仅输出 JSON，不要任何额外解释、不要 markdown 代码块。"""


@dataclass
class LLMIntentClassifier:
    """Concrete implementation of :class:`services.protocols.IntentClassifier`."""

    llm: LLMClient
    history_window: int = 2
    fallback_confidence: float = 0.3

    async def classify(
        self,
        message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> ClassifyResult:
        # Trim history per D8: last N user+assistant turns, no further.
        ctx = self._trim_history(history or [])
        ctx.append({"role": "user", "content": message})

        try:
            result: ClassifyResult = await self.llm.complete_json(
                INTENT_CLASSIFIER_SYSTEM,
                ctx,
                schema=ClassifyResult,
                temperature=0.0,
            )
        except LLMError:
            return ClassifyResult(
                intent=Intent.CASUAL,
                confidence=self.fallback_confidence,
                reason="llm_error_fallback",
            )

        if result.intent not in Intent:
            return ClassifyResult(
                intent=Intent.CASUAL,
                confidence=self.fallback_confidence,
                reason="unknown_intent_fallback",
            )
        return result

    def _trim_history(self, history: Sequence[dict[str, str]]) -> list[dict[str, str]]:
        # Keep only the last `history_window` user+assistant pairs.
        max_msgs = max(0, self.history_window) * 2
        if max_msgs == 0:
            return []
        return [dict(m) for m in list(history)[-max_msgs:]]
