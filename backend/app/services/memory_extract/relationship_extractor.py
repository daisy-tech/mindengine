"""LLM-driven relationship extraction.

Per docs/rebuild/05-Subsystem-Memory-Layers.md §6.2 + lesson 4.3:
- LLM emits {name, role, attributes, via_name?} candidates.
- Worker is responsible for resolving ``via_name`` (a string) into a
  parent ``relationships.id`` via the repo, *and* for enforcing the
  no-self-loop invariant. The extractor only validates that
  ``via_name != name`` so we never propose a self-loop in the first place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

RELATIONSHIP_EXTRACTOR_SYSTEM = """你是关系抽取器。
任务：从对话最后两轮里，抽出用户提到的**人物关系**。

输出 JSON：
{ "relationships": [
    {
      "name": "对方的名字或称谓（妻子张三、儿子小宝、朋友老李……）",
      "role": "妻子|儿子|女儿|父亲|母亲|同事|朋友|领导|...",
      "attributes": { "key": "value" },   // 可选，比如 {"职业": "医生", "年龄": "35"}
      "via_name": "通过哪一段已有关系认识，可选；不能等于自己"
    }
] }

严格要求：
- 用户**自己**不要写进关系（"我"不是关系）。
- 同一段话里同一个人只出现一次，重复合并。
- 没有任何关系输出 {"relationships": []}。
- via_name 必须不等于 name（不能自指）。
- 仅输出 JSON。"""


class RelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=50)
    attributes: dict[str, str] = Field(default_factory=dict)
    via_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_via(self) -> RelationshipCandidate:
        if self.via_name and self.via_name.strip() == self.name.strip():
            raise ValueError("via_name must differ from name (no self-loop)")
        return self


class RelationshipExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relationships: list[RelationshipCandidate] = Field(default_factory=list)


@dataclass
class RelationshipExtractor:
    llm: LLMClient
    history_window: int = 2
    max_per_turn: int = 6

    async def extract(
        self,
        user_message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> list[RelationshipCandidate]:
        ctx = self._build_messages(user_message, history or [])
        try:
            result = await self.llm.complete_json(
                RELATIONSHIP_EXTRACTOR_SYSTEM,
                ctx,
                schema=RelationshipExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return []
        except Exception:
            return []
        if not isinstance(result, RelationshipExtractionResult):
            return []

        out: list[RelationshipCandidate] = []
        seen: set[str] = set()
        for cand in result.relationships:
            key = (cand.name.strip().lower(), cand.role.strip().lower())
            sig = "::".join(key)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(cand)
            if len(out) >= self.max_per_turn:
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
