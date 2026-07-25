"""Request/response schemas for user accounts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class UserRead(ORMModel):
    """Public representation of a user account (mirrors ``lib/types.ts`` ``User``)."""

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    avatar_url: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserUpdate(BaseModel):
    """Fields a user may change on their own profile.

    Only keys that are explicitly supplied are applied (``exclude_unset``), so a
    partial ``PATCH`` never clobbers untouched fields.
    """

    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=1024)
