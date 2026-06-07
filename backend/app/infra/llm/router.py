"""Role-based LLM client dispatcher.

Per docs/rebuild/09-LLM-Strategy.md §1.3: switching models = swapping
the entry in the role→client map; business code never touches the
concrete client.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.llm import LLMRole
from app.services.protocols import LLMClient


@dataclass
class LLMRouter:
    """Holds one LLMClient per role.

    Construction is performed once at app startup (or once per worker)
    and injected into ChatOrchestrator / classifiers / extractors.
    """

    clients: dict[LLMRole, LLMClient] = field(default_factory=dict)

    def register(self, role: LLMRole, client: LLMClient) -> None:
        self.clients[role] = client

    def for_role(self, role: LLMRole) -> LLMClient:
        try:
            return self.clients[role]
        except KeyError as e:
            raise RuntimeError(
                f"LLMRouter: no client registered for role={role!r}; "
                f"registered={sorted(self.clients)}"
            ) from e

    def has(self, role: LLMRole) -> bool:
        return role in self.clients
