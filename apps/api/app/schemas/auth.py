"""Request/response schemas for the authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class Tokens(BaseModel):
    """A freshly issued JWT pair. ``access_token`` carries the active org."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    org_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str
    # Optional: resume into this org (the caller's active org) instead of the default one,
    # so a silent token refresh preserves which organization the user is working in. Honored
    # only when the user has an ACTIVE membership there.
    org_id: uuid.UUID | None = None


class LogoutRequest(BaseModel):
    """Optional body for ``POST /auth/logout``.

    When a refresh token is supplied it is revoked server-side; clients that send no
    body keep the old discard-your-tokens behaviour.
    """

    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    """Body for ``POST /auth/change-password``.

    The new password follows the same policy as :class:`RegisterRequest`.
    """

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
