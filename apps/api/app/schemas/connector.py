"""Pydantic schemas for connector CRUD.

A :class:`~app.models.connector.Connector` points an organization at an LLM provider
(OpenAI-compatible, Anthropic, or Google Gemini) for a single
:class:`~app.models.enums.ConnectorPurpose` (embedding or completion).

Credentials are **write-only**: they are accepted on create/update, encrypted at rest
via :func:`app.core.security.encrypt_secret`, and are NEVER serialized back to a client.
``has_credentials`` lets the UI show whether a secret is stored without revealing it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.connector import Connector
from app.models.enums import ConnectorPurpose, ConnectorType
from app.schemas.common import ORMModel


class ConnectorBase(BaseModel):
    """Fields common to create/read (non-secret)."""

    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(min_length=1, max_length=255)
    type: ConnectorType
    purpose: ConnectorPurpose
    model: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret provider options (base_url, api_version, region…).",
    )


class ConnectorCreate(ConnectorBase):
    credentials: dict[str, Any] | None = Field(
        default=None,
        description='Secret credential map, e.g. {"api_key": "…"}. Encrypted at rest.',
    )
    is_default: bool = Field(
        default=False,
        description="Make this the default connector for its (org, purpose).",
    )
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    """Partial update. Any omitted field is left unchanged.

    Passing ``credentials`` replaces the stored secret; passing an empty object
    (``{}``) clears it.
    """

    model_config = ConfigDict(protected_namespaces=())

    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: ConnectorType | None = None
    purpose: ConnectorPurpose | None = None
    model: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = None
    is_default: bool | None = None
    enabled: bool | None = None


class ConnectorRead(ORMModel):
    """Public, secret-free representation of a connector."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    name: str
    type: ConnectorType
    purpose: ConnectorPurpose
    model: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool
    enabled: bool
    has_credentials: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, connector: Connector) -> ConnectorRead:
        """Build the read model from an ORM row, deriving ``has_credentials``."""
        return cls(
            id=connector.id,
            name=connector.name,
            type=connector.type,
            purpose=connector.purpose,
            model=connector.model,
            config=connector.config or {},
            is_default=connector.is_default,
            enabled=connector.enabled,
            has_credentials=bool(connector.encrypted_credentials),
            created_at=connector.created_at,
            updated_at=connector.updated_at,
        )


class ConnectorTestResult(BaseModel):
    """Result of a live provider round-trip triggered by ``POST /connectors/{id}/test``."""

    ok: bool
    message: str
    latency_ms: int | None = None
