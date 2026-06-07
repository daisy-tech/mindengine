"""Deterministic personality-contract enforcement.

Per docs/rebuild/04-Subsystem-Personality.md §9.4 (D3).

The guard runs *after* the LLM has produced its full reply. It performs
two narrow operations:

1. **Length truncation** — if the reply exceeds ``contract.max_chars``,
   cut at the last sentence boundary that still fits, falling back to a
   hard char cut + ellipsis if no boundary is found.
2. **Trailing question stripping (introvert only)** — if the contract
   forbids questions and the last sentence ends with "?" or "？", drop
   that sentence outright.

Both operations record themselves in :class:`ContractEnforcement` so the
eval lab can quantify how often the raw model output already complies
(D3 acceptance criterion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.prompt import ContractEnforcement
from app.domain.route import Personality
from app.services.prompt_composer.personality import (
    PersonalityContract,
    contract_for,
)

# Sentence terminators (CJK + western).
_SENTENCE_END = "。！!？?；;\n"
_QUESTION_END = "？?"


@dataclass(frozen=True)
class EnforceResult:
    text: str
    enforcement: ContractEnforcement


@dataclass
class ContractGuard:
    """Stateless service. One instance per process is fine."""

    def enforce(self, raw: str, personality: Personality) -> EnforceResult:
        contract = contract_for(personality)
        report = ContractEnforcement(raw_char_count=len(raw or ""))

        text = (raw or "").strip()
        if not text:
            report.final_char_count = 0
            return EnforceResult(text="", enforcement=report)

        # Step 1: trailing-question strip (introvert only).
        if not contract.allow_question and self._ends_with_question(text):
            text = self._strip_trailing_question(text)
            report.removed_question = True

        # Step 2: hard length cap.
        if len(text) > contract.max_chars:
            text = self._truncate_to_max(text, contract.max_chars)
            report.truncated = True

        report.final_char_count = len(text)
        return EnforceResult(text=text, enforcement=report)

    # ─── helpers ─────────────────────────────────────────────────

    @staticmethod
    def _ends_with_question(text: str) -> bool:
        # ignore trailing whitespace + closing quotes / brackets
        stripped = re.sub(r"[\s\)\]\"'』）】」]+$", "", text)
        return bool(stripped) and stripped[-1] in _QUESTION_END

    @staticmethod
    def _strip_trailing_question(text: str) -> str:
        """Remove the LAST sentence if it ends in '?' / '？'.

        We look back for the previous sentence terminator and keep
        everything up to (and including) that terminator. If there is
        no earlier terminator, the entire text is one question — return
        empty string rather than a half-sentence.
        """
        end_idx = len(text)
        # Walk back over trailing punctuation/space.
        while end_idx > 0 and text[end_idx - 1] in _SENTENCE_END + " \t\r\n":
            end_idx -= 1
        # Find the previous sentence terminator before end_idx.
        prev = -1
        for i in range(end_idx - 1, -1, -1):
            if text[i] in _SENTENCE_END:
                prev = i
                break
        if prev == -1:
            return ""
        return text[: prev + 1].rstrip()

    @staticmethod
    def _truncate_to_max(text: str, max_chars: int) -> str:
        """Cut at the last sentence boundary that fits within ``max_chars``.

        Falls back to a hard char cut + "…" only when no boundary fits.
        """
        if len(text) <= max_chars:
            return text
        head = text[:max_chars]
        # Find last sentence-end inside head.
        for i in range(len(head) - 1, -1, -1):
            if head[i] in _SENTENCE_END:
                return head[: i + 1].rstrip()
        # No clean boundary — hard cut.
        return head.rstrip() + "…"


def build_guard() -> ContractGuard:
    return ContractGuard()


__all__ = ["ContractGuard", "EnforceResult", "PersonalityContract", "build_guard"]
