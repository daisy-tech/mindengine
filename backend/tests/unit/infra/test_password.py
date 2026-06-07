"""Argon2id password hasher tests."""

from __future__ import annotations

import pytest

from app.infra.auth import (
    UnsupportedHashError,
    create_default_hasher,
)


@pytest.fixture(scope="module")
def hasher():
    # Build once — argon2 with our params is ~50 ms / hash.
    return create_default_hasher()


def test_hash_starts_with_argon2id(hasher) -> None:
    hashed = hasher.hash("correct horse battery staple")
    assert hashed.startswith("$argon2id$")


def test_verify_correct_password(hasher) -> None:
    hashed = hasher.hash("hello world!")
    assert hasher.verify(hashed, "hello world!") is True


def test_verify_wrong_password(hasher) -> None:
    hashed = hasher.hash("hello world!")
    assert hasher.verify(hashed, "Hello world!") is False
    assert hasher.verify(hashed, "") is False


def test_empty_password_rejected(hasher) -> None:
    with pytest.raises(ValueError):
        hasher.hash("")


def test_non_argon2_hash_rejected(hasher) -> None:
    # Lesson 9.5: never accept legacy bcrypt / sha256 hashes.
    with pytest.raises(UnsupportedHashError):
        hasher.verify("$2b$12$abcdefghijklmnopqrstuv", "anything")


def test_malformed_argon2_hash_rejected(hasher) -> None:
    with pytest.raises(UnsupportedHashError):
        hasher.verify("$argon2id$blah-blah", "anything")


def test_needs_rehash_false_for_fresh_hash(hasher) -> None:
    hashed = hasher.hash("foo bar baz")
    assert hasher.needs_rehash(hashed) is False
