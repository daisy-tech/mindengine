"""LLM-driven episodic-fact extraction.

Per docs/rebuild/05-Subsystem-Memory-Layers.md §5.2 (the "infer=False"
analogue of legacy Mem0): the LLM produces clean fact fragments — short,
self-contained, embeddable strings — which the worker then embeds and
inserts into ``episodic_memories``.

This deliberately avoids storing raw user turns: those have noise and
indexing the noise is exactly what produced the v0.97 retrieval mess.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

EPISODIC_EXTRACTOR_SYSTEM = """你是事实片段抽取器。
任务：从对话最后两轮里，抽出能在未来"想起来"的**事实片段**。

每条片段：
- 一句完整的、第三人称的事实陈述（用户称作"用户"）。
- 不超过 60 字，自包含（脱离上下文也能看懂）。
- 不带主观评价、不带助手回应。
- 不要重复 profile / events 已经管的信息（姓名、生日、近期经历等抽事件的）。
- 适合做**长期回忆**：兴趣、习惯、偏好、人物、地点、口头禅等。

**重要：不要复述上下文里"早就讲过"的旧事实。**
- 如果一个事实在 history（前一轮 user/assistant 文本）里已经被提到过，
  本轮**不要再抽出来**——后端会把它当作重复跳过，徒增噪音。
- 只抽**最后一句 user 消息里第一次出现**的新增信息。
- 例：history 里已经说过"用户养了一只叫奶黄的猫"，本轮 user 又说"奶黄
  最近粘着我"——只抽"奶黄最近粘着用户"，不要再抽"用户养了一只叫奶黄
  的猫"。

例：
"用户的儿子喜欢台球，把它列为第二运动"
"用户每周固定和儿子去打台球加吃大餐"
"用户在家有泡茶的习惯"

输出 JSON：
{ "facts": [str, ...] }

如果没有可抽的片段，输出 {"facts": []}。仅输出 JSON。"""


class EpisodicExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[str] = Field(default_factory=list)


@dataclass
class EpisodicExtractor:
    llm: LLMClient
    history_window: int = 2
    max_facts_per_turn: int = 6
    min_fact_length: int = 4
    max_fact_length: int = 240

    async def extract(
        self,
        user_message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> list[str]:
        ctx = self._build_messages(user_message, history or [])
        try:
            result = await self.llm.complete_json(
                EPISODIC_EXTRACTOR_SYSTEM,
                ctx,
                schema=EpisodicExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return []
        except Exception:
            return []
        if not isinstance(result, EpisodicExtractionResult):
            return []

        out: list[str] = []
        seen: set[str] = set()
        for raw in result.facts:
            if not isinstance(raw, str):
                continue
            cleaned = raw.strip()
            if len(cleaned) < self.min_fact_length:
                continue
            if len(cleaned) > self.max_fact_length:
                cleaned = cleaned[: self.max_fact_length].rstrip()
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cleaned)
            if len(out) >= self.max_facts_per_turn:
                break
        return out

    def _build_messages(
        self,
        user_message: str,
        history: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        max_msgs = max(0, self.history_window) * 2
        clipped = list(history)[-max_msgs:] if max_msgs else []
        clipped.append({"role": "user", "content": user_message})
        return clipped
