"""LLM call exception taxonomy (doc 09 §5.1)."""

from __future__ import annotations


class LLMError(Exception):
    """Base for LLM call errors."""


class LLMRateLimitError(LLMError):
    """Vendor-imposed throttling. Retry with exponential backoff."""


class LLMTimeoutError(LLMError):
    """Network or vendor-side timeout."""


class LLMNetworkError(LLMError):
    """Generic network failure."""


class LLMContentFilterError(LLMError):
    """Vendor content moderation tripped. Surface friendly message to user."""


class LLMInvalidJSONError(LLMError):
    """Structured-output parse failure. Caller should fall back gracefully."""
