"""Auth primitives — argon2id password hashing + JWT.

Per docs/rebuild/02-TDD.md §6 + 09-LLM-Strategy.md §9.1 (D6).
"""

from app.infra.auth.jwt_codec import (
    JwtCodec,
    JwtError,
    JwtExpiredError,
    JwtInvalidError,
)
from app.infra.auth.password import (
    PasswordHasher,
    UnsupportedHashError,
    create_default_hasher,
)

__all__ = [
    "JwtCodec",
    "JwtError",
    "JwtExpiredError",
    "JwtInvalidError",
    "PasswordHasher",
    "UnsupportedHashError",
    "create_default_hasher",
]
