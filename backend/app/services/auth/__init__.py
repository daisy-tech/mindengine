"""Auth service: register / login.

Per docs/rebuild/02-TDD.md §6 + 09 §9.1.
"""

from app.services.auth.service import (
    AuthError,
    AuthResult,
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
)

__all__ = [
    "AuthError",
    "AuthResult",
    "AuthService",
    "DuplicateEmailError",
    "InvalidCredentialsError",
]
