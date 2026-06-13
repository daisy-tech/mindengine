"""AuthService tests using FakeUserStore."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.route import Personality
from app.infra.auth import JwtCodec, create_default_hasher
from app.services.auth import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
)
from app.services.auth.service import WeakPasswordError
from tests.unit.services._fakes import FakeUserStore


@pytest.fixture(scope="module")
def hasher():
    return create_default_hasher()


@pytest.fixture
def jwt_codec():
    return JwtCodec(secret="test-secret", default_ttl=timedelta(minutes=10))


@pytest.fixture
def service(hasher, jwt_codec):
    return AuthService(users=FakeUserStore(), hasher=hasher, jwt=jwt_codec)


@pytest.mark.asyncio
async def test_register_then_login_succeeds(service, jwt_codec) -> None:
    result = await service.register(
        email="A@Example.COM ",
        password="hunter22-strong",
        display_name="alice",
    )
    assert result.user_id
    assert result.access_token
    assert result.expires_in_seconds == 600

    # Token decodes to the user.
    assert jwt_codec.decode(result.access_token) == result.user_id

    # Login with the email (and a different case) works.
    login = await service.login(email="a@example.com", password="hunter22-strong")
    assert login.user_id == result.user_id


@pytest.mark.asyncio
async def test_register_duplicate_email(service) -> None:
    await service.register(email="x@example.com", password="hunter22-strong")
    with pytest.raises(DuplicateEmailError):
        await service.register(email="x@example.com", password="hunter22-strong")


@pytest.mark.asyncio
async def test_login_wrong_password(service) -> None:
    await service.register(email="bob@example.com", password="hunter22-strong")
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="bob@example.com", password="WRONG")


@pytest.mark.asyncio
async def test_login_unknown_email(service) -> None:
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="ghost@example.com", password="anything!!")


@pytest.mark.asyncio
async def test_weak_password_rejected(service) -> None:
    with pytest.raises(WeakPasswordError):
        await service.register(email="weak@example.com", password="123")


@pytest.mark.asyncio
async def test_invalid_email_rejected(service) -> None:
    with pytest.raises(ValueError):
        await service.register(email="not-an-email", password="hunter22-strong")


@pytest.mark.asyncio
async def test_password_hash_never_in_record(service) -> None:
    result = await service.register(email="z@example.com", password="hunter22-strong")
    user = await service.get_user(result.user_id)
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_personality_default_balanced(service) -> None:
    res = await service.register(email="p@example.com", password="hunter22-strong")
    user = await service.get_user(res.user_id)
    assert user is not None
    assert user.personality == Personality.BALANCED


@pytest.mark.asyncio
async def test_change_password_success(service) -> None:
    res = await service.register(
        email="rotate@example.com", password="hunter22-strong"
    )
    await service.change_password(
        user_id=res.user_id,
        current_password="hunter22-strong",
        new_password="brand-new-pass-9",
    )
    # Old password no longer works.
    with pytest.raises(InvalidCredentialsError):
        await service.login(email="rotate@example.com", password="hunter22-strong")
    # New password does.
    relogin = await service.login(
        email="rotate@example.com", password="brand-new-pass-9"
    )
    assert relogin.user_id == res.user_id


@pytest.mark.asyncio
async def test_change_password_wrong_current(service) -> None:
    res = await service.register(
        email="rotate2@example.com", password="hunter22-strong"
    )
    with pytest.raises(InvalidCredentialsError):
        await service.change_password(
            user_id=res.user_id,
            current_password="WRONG",
            new_password="brand-new-pass-9",
        )


@pytest.mark.asyncio
async def test_change_password_weak_new(service) -> None:
    res = await service.register(
        email="rotate3@example.com", password="hunter22-strong"
    )
    with pytest.raises(WeakPasswordError):
        await service.change_password(
            user_id=res.user_id,
            current_password="hunter22-strong",
            new_password="123",
        )


@pytest.mark.asyncio
async def test_admin_reset_password_success(service) -> None:
    res = await service.register(
        email="locked@example.com", password="hunter22-strong"
    )
    affected = await service.admin_reset_password(
        email="LOCKED@example.com",
        new_password="brand-new-pass-9",
    )
    assert affected == res.user_id
    relogin = await service.login(
        email="locked@example.com", password="brand-new-pass-9"
    )
    assert relogin.user_id == res.user_id


@pytest.mark.asyncio
async def test_admin_reset_password_unknown_user(service) -> None:
    with pytest.raises(InvalidCredentialsError):
        await service.admin_reset_password(
            email="ghost@example.com",
            new_password="brand-new-pass-9",
        )


@pytest.mark.asyncio
async def test_admin_reset_password_weak(service) -> None:
    await service.register(email="locked2@example.com", password="hunter22-strong")
    with pytest.raises(WeakPasswordError):
        await service.admin_reset_password(
            email="locked2@example.com",
            new_password="123",
        )
