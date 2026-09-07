"""Schemas for SCIM provisioning-token administration."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ScimTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ScimTokenRead(ORMModel):
    id: uuid.UUID
    name: str
    token_prefix: str
    revoked: bool
    last_used_at: datetime | None = None
    created_at: datetime


class ScimTokenCreated(ScimTokenRead):
    # The raw token is returned ONCE at creation and never stored in the clear.
    token: str
