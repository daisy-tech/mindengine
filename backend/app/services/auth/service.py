"""Auth service: register, login, fetch-by-id.

Lives in services/ and depends on infra/auth (password + JWT). The
SQLAlchemy session is passed in by the API layer so this stays unit-testable
with in-memory fake repos.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable

from app.domain.route import Personality
from app.infra.auth import JwtCodec, PasswordHasher

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8


class AuthError(Exception):
    """Base class for auth-service errors."""


class DuplicateEmailError(AuthError):
    """Email already registered."""


class InvalidCredentialsError(AuthError):
    """Email not found or password mismatch.

    NOTE: API layer must surface a single generic message for both cases
    to avoid user enumeration.
    """


class WeakPasswordError(AuthError):
    """Password doesn't meet minimum policy."""


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    password_hash: str
    display_name: str | None
    personality: Personality
    is_active: bool
    is_dev: bool


@dataclass(frozen=True)
class AuthResult:
    user_id: str
    access_token: str
    expires_in_seconds: int


@runtime_checkable
class UserStore(Protocol):
    """Storage abstraction for the auth service.

    Concrete impl: app.infra.repositories.user_repo.UserRepository.
    A simple in-memory dict can implement this for unit tests.
    """

    async def get_by_email(self, email: str) -> UserRecord | None: ...
    async def get_by_id(self, user_id: str) -> UserRecord | None: ...
    async def create(self, record: UserRecord) -> None: ...
    async def update_password_hash(self, user_id: str, new_hash: str) -> None: ...


@dataclass
class AuthService:
    users: UserStore
    hasher: PasswordHasher
    jwt: JwtCodec

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
        personality: Personality = Personality.BALANCED,
    ) -> AuthResult:
        email_norm = self._normalize_email(email)
        self._check_password_strength(password)

        existing = await self.users.get_by_email(email_norm)
        if existing is not None:
            raise DuplicateEmailError(f"Email already registered: {email_norm}")

        record = UserRecord(
            id=str(uuid.uuid4()),
            email=email_norm,
            password_hash=self.hasher.hash(password),
            display_name=display_name,
            personality=personality,
            is_active=True,
            is_dev=False,
        )
        await self.users.create(record)
        return self._issue(record.id)

    async def login(self, *, email: str, password: str) -> AuthResult:
        email_norm = self._normalize_email(email)
        user = await self.users.get_by_email(email_norm)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Email or password is incorrect")
        if not self.hasher.verify(user.password_hash, password):
            raise InvalidCredentialsError("Email or password is incorrect")
        if self.hasher.needs_rehash(user.password_hash):
            await self.users.update_password_hash(user.id, self.hasher.hash(password))
        return self._issue(user.id)

    async def get_user(self, user_id: str) -> UserRecord | None:
        return await self.users.get_by_id(user_id)

    async def change_password(
        self,
        *,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        """Self-service password rotation for an authenticated user.

        Requires the *current* password (so a stolen JWT alone cannot
        rotate the password). The new password is checked against the
        same minimum-strength policy as registration.
        """

        user = await self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("user not found")
        if not self.hasher.verify(user.password_hash, current_password):
            raise InvalidCredentialsError("current password is incorrect")
        self._check_password_strength(new_password)
        await self.users.update_password_hash(
            user_id, self.hasher.hash(new_password)
        )

    async def admin_reset_password(
        self,
        *,
        email: str,
        new_password: str,
    ) -> str:
        """Out-of-band password reset (admin/CLI).

        Used by ``scripts/reset_password.py`` when a user is locked out.
        Trust model: caller already has shell access to the host (or the
        backend container), so authentication is the shell itself — we
        deliberately do NOT expose this on the HTTP surface.

        Returns the user_id of the affected user.
        """

        email_norm = self._normalize_email(email)
        user = await self.users.get_by_email(email_norm)
        if user is None:
            raise InvalidCredentialsError(f"no user with email {email_norm!r}")
        self._check_password_strength(new_password)
        await self.users.update_password_hash(
            user.id, self.hasher.hash(new_password)
        )
        return user.id

    # ─── helpers ───────────────────────────────────────────────────

    def _issue(self, user_id: str) -> AuthResult:
        ttl = self.jwt.default_ttl
        token = self.jwt.encode(user_id=user_id, ttl=ttl)
        return AuthResult(
            user_id=user_id,
            access_token=token,
            expires_in_seconds=int(ttl.total_seconds()),
        )

    @staticmethod
    def _normalize_email(email: str) -> str:
        e = (email or "").strip().lower()
        if not _EMAIL_RE.match(e):
            raise ValueError(f"Invalid email format: {email!r}")
        return e

    @staticmethod
    def _check_password_strength(pw: str) -> None:
        # Minimal policy; productionize via 09 §9.1 in M5.
        if not pw or len(pw) < _MIN_PASSWORD_LEN:
            raise WeakPasswordError(
                f"Password must be at least {_MIN_PASSWORD_LEN} characters"
            )


# Re-export the builder used by api.deps so callers don't need to construct
# things by hand.
def custom_jwt_ttl(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)
