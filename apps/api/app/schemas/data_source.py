"""Pydantic schemas for data-source (knowledge connector) management.

``secret`` is write-only: accepted on create/update, encrypted at rest, never serialized
back. ``has_secret`` lets the UI show whether a credential is stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.datasource import DataSource
from app.models.enums import (
    DataSourceKind,
    DataSourceStatus,
    ExternalPrincipalKind,
    Visibility,
)
from app.schemas.common import ORMModel


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: DataSourceKind
    collection_id: uuid.UUID
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, description="Credential/token, encrypted at rest.")
    default_visibility: Visibility = Visibility.PRIVATE
    sync_interval_minutes: int | None = Field(default=None, ge=1)


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    secret: str | None = None
    default_visibility: Visibility | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=1)
    status: DataSourceStatus | None = Field(
        default=None, description="Set ACTIVE/PAUSED to enable/disable scheduled syncing."
    )


class DataSourceRead(ORMModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: DataSourceKind
    status: DataSourceStatus
    collection_id: uuid.UUID
    config: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool = False
    default_visibility: Visibility
    sync_interval_minutes: int | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    document_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, source: DataSource) -> DataSourceRead:
        return cls(
            id=source.id,
            name=source.name,
            kind=source.kind,
            status=source.status,
            collection_id=source.collection_id,
            config=source.config or {},
            has_secret=bool(source.encrypted_secret),
            default_visibility=source.default_visibility,
            sync_interval_minutes=source.sync_interval_minutes,
            last_synced_at=source.last_synced_at,
            last_error=source.last_error,
            document_count=source.document_count,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )


class SyncResult(BaseModel):
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0


class ExternalPrincipalItem(BaseModel):
    """A distinct source principal seen across a source's documents, with mapping status."""

    provider: str
    external_id: str
    kind: ExternalPrincipalKind
    document_count: int
    mapped: bool
    mapped_user_id: uuid.UUID | None = None
    mapped_team_id: uuid.UUID | None = None


class IdentityMapCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=512)
    kind: ExternalPrincipalKind = ExternalPrincipalKind.USER
    user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None


class IdentityRead(ORMModel):
    id: uuid.UUID
    provider: str
    external_id: str
    kind: ExternalPrincipalKind
    user_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    created_at: datetime


class IdentityMapResult(BaseModel):
    identity: IdentityRead
    grants_backfilled: int
