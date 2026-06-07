"""Qwen / DashScope client (OpenAI-compatible).

Per docs/rebuild/09-LLM-Strategy.md §2.1 + lesson 7.1:
- enable_thinking is forced to False on every call (no opt-in needed)
- streaming yields plain str chunks; vendor quirks (empty role-only first
  chunk, trailing usage-only chunk) are absorbed here
- complete_json strips ```json fences and validates against a Pydantic schema

The body is intentionally minimal in M1 — M2 will add tenacity retries,
trace logging, and cost accounting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from openai import APIError as _OpenAIAPIError
from openai import AsyncOpenAI

from app.domain.llm import LLMRole
from app.infra.llm.exceptions import (
    LLMInvalidJSONError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.infra.llm.utils import strip_json_fences


@dataclass
class QwenClient:
    """Wraps an OpenAI-compatible AsyncOpenAI for one role + one model.

    The DashScope endpoint accepts the standard ChatCompletion API; the
    only vendor-specific bit is the `enable_thinking` flag in extra_body.
    """

    role: LLMRole
    model: str
    client: AsyncOpenAI
    enable_thinking: bool = False
    request_timeout_s: float = 60.0

    def _extra_body(self) -> dict[str, Any]:
        # We always send the flag — even if vendor default flips later.
        return {"enable_thinking": self.enable_thinking}

    @staticmethod
    def _to_messages(
        system: str, messages: Sequence[dict[str, str]]
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(dict(m) for m in messages)
        return out

    @staticmethod
    def _wrap(e: Exception) -> Exception:
        if isinstance(e, _OpenAIAPIError):
            status = getattr(e, "status_code", None)
            if status == 429:
                return LLMRateLimitError(str(e))
            if status in (408, 504):
                return LLMTimeoutError(str(e))
        if isinstance(e, TimeoutError):
            return LLMTimeoutError(str(e))
        return LLMNetworkError(str(e))

    # ------------------------------------------------------------------ Protocol

    async def complete(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=self._to_messages(system, messages),
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=self._extra_body(),
                timeout=self.request_timeout_s,
                stream=False,
            )
        except Exception as e:
            raise self._wrap(e) from e
        choice = resp.choices[0]
        return (choice.message.content or "").strip()

    async def stream(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        return self._stream_impl(system, messages, temperature, max_tokens)

    async def _stream_impl(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=self._to_messages(system, messages),
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=self._extra_body(),
                timeout=self.request_timeout_s,
                stream=True,
            )
        except Exception as e:
            raise self._wrap(e) from e

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue  # vendor quirk: usage-only trailing chunk
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as e:
            raise self._wrap(e) from e

    async def complete_json(
        self,
        system: str,
        messages: Sequence[dict[str, str]],
        *,
        schema: type[Any],
        temperature: float = 0.0,
    ) -> Any:
        # Many vendors honor response_format; pass it but tolerate vendors
        # that ignore it (we still strip fences below).
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=self._to_messages(system, messages),
                temperature=temperature,
                response_format={"type": "json_object"},
                extra_body=self._extra_body(),
                timeout=self.request_timeout_s,
                stream=False,
            )
        except Exception as e:
            raise self._wrap(e) from e

        raw = (resp.choices[0].message.content or "").strip()
        cleaned = strip_json_fences(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMInvalidJSONError(f"Could not parse JSON from model: {raw!r}") from e
        try:
            return schema(**data)
        except Exception as e:
            raise LLMInvalidJSONError(
                f"Model JSON does not match schema {schema.__name__}: {data!r}"
            ) from e
