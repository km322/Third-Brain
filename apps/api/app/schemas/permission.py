"""Pydantic schemas for the access-grant (permissions) endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.enums import PermissionLevel, PrincipalType, ResourceType
from app.schemas.common import ORMModel


class AccessGrantRead(ORMModel):
    """An explicit ACL entry, with the principal's display name resolved."""

    id: uuid.UUID
    resource_type: ResourceType
    resource_id: uuid.UUID
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: PermissionLevel
    principal_name: str | None = None


class AccessGrantCreate(BaseModel):
    resource_type: ResourceType
    resource_id: uuid.UUID
    principal_type: PrincipalType
    principal_id: uuid.UUID
    permission: PermissionLevel


class EffectivePermissionRead(BaseModel):
    """The caller's computed effective permission on a resource."""

    permission: PermissionLevel
