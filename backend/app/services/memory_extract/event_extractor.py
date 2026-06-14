"""LLM-driven event extraction (5 types: experience/plan/milestone/challenge/reflection).

Per docs/rebuild/05-Subsystem-Memory-Layers.md §4.

The model returns a list of EventCandidate objects; the worker decides
which to actually persist (filter by user_id, attach source_message_id,
deprecate stale, etc.).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

EventType = Literal["experience", "plan", "milestone", "challenge", "reflection"]


EVENT_EXTRACTOR_SYSTEM = """你是事件抽取器。
任务：从对话最后两轮里抽出用户**亲身经历或确定要做**的事件。

每个事件分以下五类（type 必填）：
- experience：已经发生过的具体事，比如"周末和儿子吃饭"
- plan：未来的计划，比如"下周一面试 X 公司"
- milestone：里程碑/重大事件，比如"通过驾考"
- challenge：困境/压力，比如"最近工作压力大"
- reflection：自我反省/觉察，比如"意识到自己更适合写作"

输出 JSON：
{ "events": [
    {
      "type": "experience|plan|milestone|challenge|reflection",
      "title": "≤20 字短标题",
      "content": "完整描述，单句即可",
      "occurred_at": "ISO 时间字符串，可选"
    }
] }

严格要求：
- 只抽用户自己说的事；助手的回应/客套话忽略。
- 同一句话不要拆成多个事件。
- 模糊的、"我可能会..." 这种不写。
- **不要复述 history 里已经讲过的事件**：如果 user 上一轮已经讲过
  "宅家学习AI"，本轮再次提及时不要再抽出来；只抽**新增信息或更新**。
- title 用最简短描述，不要在结尾加句号、感叹号等标点（"宅家学习AI"
  而不是"宅家学习AI。"）。
- 没有事件就 {"events": []}。
- 仅输出 JSON。"""


class EventCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: EventType
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)
    occurred_at: datetime | None = None


class EventExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[EventCandidate] = Field(default_factory=list)


@dataclass
class EventExtractor:
    llm: LLMClient
    history_window: int = 2
    max_events_per_turn: int = 5

    async def extract(
        self,
        user_message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> list[EventCandidate]:
        ctx = self._build_messages(user_message, history or [])
        try:
            result = await self.llm.complete_json(
                EVENT_EXTRACTOR_SYSTEM,
                ctx,
                schema=EventExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return []
        except Exception:
            return []
        if not isinstance(result, EventExtractionResult):
            return []
        # Cap to avoid runaway extraction (one user turn rarely has >5 events).
        return result.events[: self.max_events_per_turn]

    def _build_messages(
        self,
        user_message: str,
        history: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        max_msgs = max(0, self.history_window) * 2
        clipped = list(history)[-max_msgs:] if max_msgs else []
        clipped.append({"role": "user", "content": user_message})
        return clipped
