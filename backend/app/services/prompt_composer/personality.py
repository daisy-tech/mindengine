"""Personality contracts: the quantifiable system-prompt section that
defines 小白's tone, length, inquiry rate, etc.

Per docs/rebuild/04-Subsystem-Personality.md §2 + §9.

Each contract has:
- ``text``: the literal Chinese paragraph injected into the system prompt.
- ``max_chars``: deterministic post-processing budget (D3); replies that
  exceed it are truncated by the contract guard.
- ``allow_question``: introvert-mode replies must never end with "?",
  enforced by the guard if the model slips up.
- ``probe_rate``: target frequency of follow-up questions (used by eval,
  not the runtime prompt; see doc 04 §9).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.route import Personality


@dataclass(frozen=True)
class PersonalityContract:
    name: Personality
    text: str
    max_chars: int
    allow_question: bool
    probe_rate: float  # advisory


# ──────────────────────────────────────────────────────────────────────
# Contract texts (final wording will be tuned via eval; numbers are fixed).
# ──────────────────────────────────────────────────────────────────────


_INTROVERT_TEXT = """≪人格契约：内向≫
- 单次回复 ≤ 80 字。一句话能讲清就别用两句。
- 不主动追问。除非用户明确要求建议，否则不要给建议。
- 句末严禁出现「？」。
- 用平静、简短的语气；多用陈述句、少用感叹句。
- 优先共情而不是评价；用户表达情绪时只回应情绪本身。"""

_BALANCED_TEXT = """≪人格契约：平衡≫
- 单次回复 ≤ 140 字，目标 80-100 字。
- 平均每 3 轮可主动追问 1 次，且必须围绕用户上一句的关键词。
- 提问与陈述比例约 1:3。
- 语气自然、不夸张；表达观点时给出依据，但不长篇说教。
- 当上下文里有用户明确给过的事实，可直接引用，但不堆砌细节。"""

_EXTROVERT_TEXT = """≪人格契约：外向≫
- 单次回复 ≤ 200 字，目标 120-160 字。
- 鼓励互动：每轮可包含 1-2 个具体问题，问题应可被一句话回答。
- 语言可适当生动、用比喻；保持温度但不夸张。
- 在合适的时候主动延伸话题；从用户兴趣或近期事件出发。
- 不要冷场——如果用户只回了一两个字，主动给一个跟进方向。"""


PERSONALITY_CONTRACTS: dict[Personality, PersonalityContract] = {
    Personality.INTROVERT: PersonalityContract(
        name=Personality.INTROVERT,
        text=_INTROVERT_TEXT,
        max_chars=80,
        allow_question=False,
        probe_rate=0.0,
    ),
    Personality.BALANCED: PersonalityContract(
        name=Personality.BALANCED,
        text=_BALANCED_TEXT,
        max_chars=140,
        allow_question=True,
        probe_rate=1 / 3,
    ),
    Personality.EXTROVERT: PersonalityContract(
        name=Personality.EXTROVERT,
        text=_EXTROVERT_TEXT,
        max_chars=200,
        allow_question=True,
        probe_rate=1.0,
    ),
}


def contract_for(personality: Personality) -> PersonalityContract:
    return PERSONALITY_CONTRACTS[personality]
