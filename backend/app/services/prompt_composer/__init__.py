"""Prompt composer: route + memory context → PromptPack.

Per docs/rebuild/09-LLM-Strategy.md §3-4 + 04-Subsystem-Personality.md.
"""

from app.services.prompt_composer.composer import PromptComposer
from app.services.prompt_composer.intent_guide import INTENT_GUIDES
from app.services.prompt_composer.personality import (
    PERSONALITY_CONTRACTS,
    PersonalityContract,
    contract_for,
)

__all__ = [
    "INTENT_GUIDES",
    "PERSONALITY_CONTRACTS",
    "PersonalityContract",
    "PromptComposer",
    "contract_for",
]
