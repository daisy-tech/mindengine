"""JWT encode/decode wrapper around python-jose.

Tokens carry the bare minimum: `sub` (user_id), `exp`, `iat`. No PII.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError


class JwtError(Exception):
    """Base for JWT errors raised by this module."""


class JwtExpiredError(JwtError):
    """Token expired (still well-formed, just past its `exp`)."""


class JwtInvalidError(JwtError):
    """Malformed or signature-mismatched token."""


@dataclass
class JwtCodec:
    """Stateless encode/decode pair.

    Construct one per process; settings are immutable.
    """

    secret: str
    algorithm: str = "HS256"
    default_ttl: timedelta = timedelta(minutes=60)

    def encode(self, *, user_id: str, ttl: timedelta | None = None) -> str:
        if not user_id:
            raise ValueError("user_id must not be empty")
        now = datetime.now(UTC)
        ttl = ttl or self.default_ttl
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str) -> str:
        """Verify token and return user_id (the `sub` claim)."""
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except ExpiredSignatureError as e:
            raise JwtExpiredError("Token has expired") from e
        except JWTError as e:
            raise JwtInvalidError(f"Invalid token: {e}") from e
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise JwtInvalidError("Token missing 'sub' claim")
        return sub
