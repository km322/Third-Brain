"""Pydantic schemas for the teams API.

A team groups users inside an organization and can be used as an ACL principal
(see :mod:`app.services.permissions`). ``slug`` is derived from the name and is
unique per organization. Teams may nest via ``parent_team_id`` (grants flow strictly
downward), and each membership carries a :class:`~app.models.enums.TeamRole` (``lead``
or ``member``) that governs who may manage the team and its sub-groups.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import TeamRole
from app.schemas.common import ORMModel


class TeamUserSummary(ORMModel):
    """The subset of user fields exposed when listing a team's members."""

    id: uuid.UUID
    email: str
    full_name: str | None = None
    avatar_url: str | None = None


class TeamMemberRead(ORMModel):
    """A single membership of a team, with the linked user and role inlined."""

    id: uuid.UUID
    user_id: uuid.UUID
    role: TeamRole
    created_at: datetime
    user: TeamUserSummary | None = None


class TeamRead(ORMModel):
    """A team as returned in list/summary responses (also used for sub-team rows)."""

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    parent_team_id: uuid.UUID | None = None
    member_count: int = 0


class TeamDetail(TeamRead):
    """A team plus its resolved membership roster and direct sub-teams."""

    members: list[TeamMemberRead] = Field(default_factory=list)
    children: list[TeamRead] = Field(default_factory=list)


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    parent_team_id: uuid.UUID | None = None


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    parent_team_id: uuid.UUID | None = None
    """Presence-sensitive: omitting it leaves the parent unchanged, while sending ``null``
    re-parents the team to the root. Callers detect the difference via ``model_fields_set``."""


class TeamMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: TeamRole = TeamRole.MEMBER


class TeamMemberRoleUpdate(BaseModel):
    role: TeamRole
