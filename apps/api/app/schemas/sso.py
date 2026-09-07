"""Schemas for SSO connection administration + the sign-in flow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import OrgRole, SsoProtocol
from app.models.sso import SsoConnection
from app.schemas.common import ORMModel


class SsoConnectionCreate(BaseModel):
    protocol: SsoProtocol
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    email_domain: str | None = Field(default=None, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    client_secret: str | None = Field(default=None, description="OIDC client secret (encrypted).")
    default_role: OrgRole = OrgRole.VIEWER


class SsoConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    email_domain: str | None = None
    config: dict[str, Any] | None = None
    client_secret: str | None = None
    default_role: OrgRole | None = None


class SsoConnectionRead(ORMModel):
    id: uuid.UUID
    protocol: SsoProtocol
    name: str
    enabled: bool
    email_domain: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool = False
    default_role: OrgRole
    created_at: datetime

    @classmethod
    def from_model(cls, c: SsoConnection) -> SsoConnectionRead:
        return cls(
            id=c.id,
            protocol=c.protocol,
            name=c.name,
            enabled=c.enabled,
            email_domain=c.email_domain,
            config=c.config or {},
            has_secret=bool(c.encrypted_secret),
            default_role=c.default_role,
            created_at=c.created_at,
        )


class SsoConnectionPublic(BaseModel):
    """Non-secret metadata shown on the login page."""

    id: uuid.UUID
    name: str
    protocol: SsoProtocol


class SsoStartResponse(BaseModel):
    url: str
    state: str
    protocol: SsoProtocol


class SsoCallbackRequest(BaseModel):
    code: str
    state: str
