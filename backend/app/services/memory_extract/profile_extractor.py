"""LLM-driven profile extraction.

Per docs/rebuild/05-Subsystem-Memory-Layers.md §3.2 and 09 §1.3.

The model sees the latest user/assistant turn(s) and emits a JSON object
that is a *partial* Profile (any subset of fields that was clearly
mentioned). The result is unioned into the existing Profile via
``ProfileMerger``.

Failure modes (LLM raises, JSON invalid, all fields empty) collapse to
"no-op" — workers must not be allowed to corrupt the profile due to a
bad LLM round.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

PROFILE_EXTRACTOR_SYSTEM = """你是用户画像抽取器。
任务：从对话最后两轮里抽出用户**明确说过**的画像信息，输出 JSON。

字段（任意子集，能抽多少抽多少；没说就不要写）：
{
  "basic": {"name": str, "birth_year": int, "gender": "male|female|other", "location": str},
  "occupation": {"title": str, "industry": str, "level": str},
  "interests": [str],
  "family_structure": {"structure": str, "notes": [str]},
  "extra": {key: str}
}

严格要求：
- 只抽用户**自己说**的事实；助手的话、推测、客套话一律忽略。
- 不存在的字段不要出现在 JSON（不要写 null）。
- 如果整段对话没有任何画像信息，输出 {}。
- 仅输出 JSON，不要 markdown，不要解释。"""


class ProfileExtractionResult(BaseModel):
    """Strict shape for the LLM's response."""

    model_config = ConfigDict(extra="ignore")

    basic: dict[str, Any] = Field(default_factory=dict)
    occupation: dict[str, Any] = Field(default_factory=dict)
    interests: list[str] = Field(default_factory=list)
    family_structure: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_partial(self) -> dict[str, Any]:
        """Return a dict suitable for ProfileMerger.merge()."""
        out: dict[str, Any] = {}
        if self.basic:
            out["basic"] = dict(self.basic)
        if self.occupation:
            out["occupation"] = dict(self.occupation)
        if self.interests:
            out["interests"] = list(self.interests)
        if self.family_structure:
            out["family_structure"] = dict(self.family_structure)
        if self.extra:
            out["extra"] = dict(self.extra)
        return out


@dataclass
class ProfileExtractor:
    llm: LLMClient
    history_window: int = 2

    async def extract(
        self,
        user_message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Returns a partial dict; empty {} when nothing was extracted.

        On any LLM error / parse error returns {} (worker must treat that
        as "skip merge"). The function never raises.
        """
        ctx = self._build_messages(user_message, history or [])
        try:
            result = await self.llm.complete_json(
                PROFILE_EXTRACTOR_SYSTEM,
                ctx,
                schema=ProfileExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return {}
        except Exception:
            return {}
        if not isinstance(result, ProfileExtractionResult):
            return {}
        return result.to_partial()

    def _build_messages(
        self,
        user_message: str,
        history: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        # Clip history to last N user/assistant turns (D8).
        max_msgs = max(0, self.history_window) * 2
        clipped = list(history)[-max_msgs:] if max_msgs else []
        clipped.append({"role": "user", "content": user_message})
        return clipped


# Re-export Literal for callers that want strict typing on gender.
Gender = Literal["male", "female", "other"]
