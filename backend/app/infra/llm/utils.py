"""Tiny utilities shared by LLM clients.

Lesson 7.2: vendors occasionally wrap JSON in ```json ... ``` fences;
strip them before json.loads.
"""

from __future__ import annotations


def strip_json_fences(s: str) -> str:
    """Remove leading / trailing markdown fences from a JSON string.

    Examples
    --------
    >>> strip_json_fences("```json\\n{\"a\": 1}\\n```")
    '{"a": 1}'
    >>> strip_json_fences('{"a": 1}')
    '{"a": 1}'
    """

    s = s.strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s.strip()
