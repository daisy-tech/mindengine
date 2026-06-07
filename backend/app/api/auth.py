"""Auth endpoints: register / login / me.

Per docs/rebuild/02-TDD.md §6.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentUserId,
    SessionDep,
    SettingsDep,
)
from app.domain.route import Personality
from app.infra.auth import create_default_hasher
from app.infra.auth.jwt_codec import JwtCodec
from app.infra.repositories import UserRepo
from app.services.auth import (
    AuthResult,
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from app.services.auth.service import WeakPasswordError

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── request/response shapes ─────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=100)
    personality: Personality = Personality.BALANCED


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    personality: Personality


# ─── helper ───────────────────────────────────────────────────────


def _build_service(session, settings) -> AuthService:
    return AuthService(
        users=UserRepo(session=session),
        hasher=create_default_hasher(),
        jwt=JwtCodec(
            secret=settings.jwt_secret,
            algorithm=settings.jwt_alg,
            default_ttl=timedelta(minutes=settings.jwt_ttl_min),
        ),
    )


def _to_token(result: AuthResult) -> TokenResponse:
    return TokenResponse(
        user_id=result.user_id,
        access_token=result.access_token,
        expires_in=result.expires_in_seconds,
    )


# ─── endpoints ────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    service = _build_service(session, settings)
    try:
        result = await service.register(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            personality=body.personality,
        )
    except DuplicateEmailError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from e
    except WeakPasswordError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except ValueError as e:
        # Service-level format validation (e.g. invalid email regex).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return _to_token(result)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    service = _build_service(session, settings)
    try:
        result = await service.login(email=body.email, password=body.password)
    except InvalidCredentialsError as e:
        # Single generic message — avoid user enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect",
        ) from e
    return _to_token(result)


@router.get("/me", response_model=MeResponse)
async def me(
    user_id: CurrentUserId,
    session: SessionDep,
    settings: SettingsDep,
) -> MeResponse:
    service = _build_service(session, settings)
    user = await service.get_user(user_id)
    if user is None:
        # Token was valid but user has been deleted — treat as 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return MeResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        personality=user.personality,
    )
