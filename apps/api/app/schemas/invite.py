"""Schemas for email-based org invitations."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import InviteStatus, OrgRole
from app.schemas.common import ORMModel


class InviteCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.VIEWER


class InviteRead(ORMModel):
    id: uuid.UUID
    email: str
    role: OrgRole
    status: InviteStatus
    expires_at: datetime
    created_at: datetime


class InviteAccept(BaseModel):
    token: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=256)
