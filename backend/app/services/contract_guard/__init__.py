"""Personality contract enforcement (post-LLM).

Per docs/rebuild/04-Subsystem-Personality.md §9.4 (D3).
"""

from app.services.contract_guard.enforcer import ContractGuard, EnforceResult

__all__ = ["ContractGuard", "EnforceResult"]
