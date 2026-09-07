"""Pydantic schemas for the API-keys endpoints.

Only the key *prefix* and a SHA-256 *hash* are ever persisted; the raw secret is
returned exactly once at creation time inside :class:`ApiKeyCreated`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# Coarse permissions an API key may carry. Mirrors the mapping used by
# ``app.core.deps._role_from_scopes``. ``*`` grants everything.
ALLOWED_SCOPES: frozenset[str] = frozenset({"read", "write", "search", "ingest", "manage", "*"})


class ApiKeyRead(ORMModel):
    """A stored API key. The secret is never included here."""

    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_per_minute: int
    revoked: bool
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    acts_as_user_id: uuid.UUID | None = None


class ApiKeyCreated(BaseModel):
    """Creation response - carries the raw secret shown to the caller once."""

    api_key: ApiKeyRead
    secret: str
