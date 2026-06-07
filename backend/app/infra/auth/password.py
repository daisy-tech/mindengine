"""Argon2id password hashing.

Per docs/rebuild/09-LLM-Strategy.md §9.1 (D6): argon2id is mandatory.
We never accept plaintext, never log password fields, and use a single
PasswordHasher singleton with conservative parameters tuned for ~50 ms
on commodity laptops (RFC 9106 §4.4 second-class profile).
"""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher as _Argon2Hasher
from argon2 import Type as _Argon2Type
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)


class UnsupportedHashError(ValueError):
    """Raised when the stored hash is not an argon2id digest.

    This guards against accidentally accepting a leftover bcrypt / sha256
    digest from a prior schema (lesson 9.5).
    """


@dataclass
class PasswordHasher:
    """Thin wrapper that encapsulates argon2id config + auto-rehash logic."""

    hasher: _Argon2Hasher

    def hash(self, password: str) -> str:
        """Hash a fresh password. The result starts with `$argon2id$...`."""
        if not password:
            raise ValueError("Password must not be empty")
        return self.hasher.hash(password)

    def verify(self, hashed: str, password: str) -> bool:
        """Constant-time verify. Returns False on mismatch (no exception)."""
        if not hashed.startswith("$argon2id$"):
            raise UnsupportedHashError(
                "Stored hash is not argon2id; refuse to verify "
                "(rotate the user out via password reset)."
            )
        try:
            return self.hasher.verify(hashed, password)
        except VerifyMismatchError:
            return False
        except InvalidHashError as e:
            raise UnsupportedHashError(f"Stored hash is malformed: {e}") from e
        except VerificationError as e:
            # argon2-cffi raises generic VerificationError for low-level
            # decode failures (e.g. truncated b64). Map it to our explicit
            # error type so callers handle it the same way.
            raise UnsupportedHashError(f"Stored hash is malformed: {e}") from e

    def needs_rehash(self, hashed: str) -> bool:
        """True when the stored hash uses outdated parameters (rotate on next login)."""
        return self.hasher.check_needs_rehash(hashed)


def create_default_hasher() -> PasswordHasher:
    """RFC 9106 §4.4 second-class profile (≈ 50 ms on commodity hardware).

    Tunable via env later; for M2 we hard-code a sensible default.
    """
    return PasswordHasher(
        _Argon2Hasher(
            time_cost=3,
            memory_cost=64 * 1024,  # 64 MiB
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=_Argon2Type.ID,
        )
    )
