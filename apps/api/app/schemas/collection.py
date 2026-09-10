"""Request/response schemas for the collections (knowledge base) API."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import PermissionLevel, Visibility
from app.schemas.common import TimestampedRead


class CollectionCreate(BaseModel):
    """Payload for creating a new knowledge base."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2048)
    visibility: Visibility = Visibility.PRIVATE
    default_permission: PermissionLevel = PermissionLevel.VIEWER
    embedding_model: str | None = Field(default=None, max_length=128)
    """Accepted only when it equals the platform embedding model; a divergent value is
    rejected because all chunks share one global vector space (see the create route)."""


class CollectionUpdate(BaseModel):
    """Partial update; only provided fields are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2048)
    visibility: Visibility | None = None
    default_permission: PermissionLevel | None = None


class CollectionRead(TimestampedRead):
    """A collection as returned to clients."""

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    visibility: Visibility
    default_permission: PermissionLevel
    embedding_model: str
    document_count: int
    owner_id: uuid.UUID | None = None
    owner_team_id: uuid.UUID | None = None
    permission: PermissionLevel | None = None
    """The caller's effective permission on this collection (populated by the route)."""
