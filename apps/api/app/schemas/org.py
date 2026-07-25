"""Request/response schemas for organizations and their memberships."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import MembershipStatus, OrgRole, PlanTier
from app.schemas.common import ORMModel
from app.schemas.user import UserRead


class OrgRead(ORMModel):
    """Public representation of an organization (mirrors ``lib/types.ts`` ``Organization``)."""

    id: uuid.UUID
    name: str
    slug: str
    plan: PlanTier
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: dict | None = None


class OrgSwitch(BaseModel):
    """Body for ``POST /orgs/switch`` - the org to make active."""

    org_id: uuid.UUID


class MembershipRead(ORMModel):
    """A user's membership in an organization, optionally with the user embedded."""

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: OrgRole
    status: MembershipStatus
    user: UserRead | None = None


class MemberInvite(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.VIEWER


class MemberUpdate(BaseModel):
    role: OrgRole | None = None
    status: MembershipStatus | None = None


class MemberPasswordReset(BaseModel):
    """Response for ``POST /orgs/members/{id}/reset-password`` - the temporary
    password, returned exactly once and never stored in the clear."""

    temporary_password: str


class CurrentUser(BaseModel):
    """Response for ``GET /users/me``: the caller, every org they belong to and the
    org that is currently active for this session (derived from the access token)."""

    user: UserRead
    organizations: list[OrgRead]
    active_org: OrgRead
    role: OrgRole
