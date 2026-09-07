"""Unit tests for :func:`authenticate_user` - credential checks and timing equalization.

Nothing here touches a database: ``get_user_by_email`` is monkeypatched to return a
canned :class:`User` (or ``None``), so these exercise the pure decision logic. The
security property under test is non-enumeration: an unknown email must still pay the
bcrypt verify cost, so a spy asserts ``verify_password`` runs even when no user exists.
There is deliberately no wall-clock assertion (flaky); the call itself is the signal.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException, status

from app.core.security import hash_password
from app.models.user import User
from app.services import auth_service

_GENERIC_401 = "Incorrect email or password"


def _user(*, password: str, is_active: bool = True) -> User:
    """A detached ``User`` with a real bcrypt hash of ``password``."""
    return User(
        email="person@example.com",
        hashed_password=hash_password(password),
        full_name="Person",
        is_active=is_active,
    )


def _patch_lookup(monkeypatch: pytest.MonkeyPatch, user: User | None) -> None:
    """Make ``get_user_by_email`` resolve to ``user`` without any DB access."""

    async def fake(db: object, email: str) -> User | None:
        return user

    monkeypatch.setattr(auth_service, "get_user_by_email", fake)


def _spy_verify(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Wrap ``verify_password`` so calls are recorded while behaviour is preserved."""
    calls: list[tuple[str, str]] = []
    real = auth_service.verify_password

    def spy(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return real(plain, hashed)

    monkeypatch.setattr(auth_service, "verify_password", spy)
    return calls


def test_unknown_email_still_verifies_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # The core timing-equalization guarantee: a login for an email that does not exist
    # must still perform a bcrypt verify so it is indistinguishable from a real account.
    calls = _spy_verify(monkeypatch)
    _patch_lookup(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_service.authenticate_user(None, "ghost@example.com", "whatever"))

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc.value.detail == _GENERIC_401
    assert len(calls) == 1


def test_user_without_password_still_verifies_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # A user row with no stored hash (e.g. SSO-only) must take the same dummy-verify path.
    calls = _spy_verify(monkeypatch)
    user = _user(password="unused")
    user.hashed_password = None
    _patch_lookup(monkeypatch, user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_service.authenticate_user(None, "person@example.com", "whatever"))

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc.value.detail == _GENERIC_401
    assert len(calls) == 1


def test_valid_credentials_return_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(password="s3cret", is_active=True)
    _patch_lookup(monkeypatch, user)

    result = asyncio.run(auth_service.authenticate_user(None, "person@example.com", "s3cret"))

    assert result is user


def test_wrong_password_raises_generic_401(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(password="s3cret")
    _patch_lookup(monkeypatch, user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_service.authenticate_user(None, "person@example.com", "wrong"))

    # Same status and message as the unknown-email case so the two are indistinguishable.
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc.value.detail == _GENERIC_401


def test_inactive_user_raises_403(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(password="s3cret", is_active=False)
    _patch_lookup(monkeypatch, user)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_service.authenticate_user(None, "person@example.com", "s3cret"))

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
