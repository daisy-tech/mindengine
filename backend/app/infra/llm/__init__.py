"""LLM clients + LLMRouter (role-based dispatch).

Per docs/rebuild/09-LLM-Strategy.md.
"""

from app.infra.llm.exceptions import (
    LLMContentFilterError,
    LLMError,
    LLMInvalidJSONError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.infra.llm.mock_client import MockLLMClient, MockTurn
from app.infra.llm.router import LLMRouter
from app.infra.llm.utils import strip_json_fences

__all__ = [
    "LLMContentFilterError",
    "LLMError",
    "LLMInvalidJSONError",
    "LLMNetworkError",
    "LLMRateLimitError",
    "LLMRouter",
    "LLMTimeoutError",
    "MockLLMClient",
    "MockTurn",
    "strip_json_fences",
]
