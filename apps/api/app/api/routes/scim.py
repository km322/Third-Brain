"""SCIM 2.0 provisioning endpoints (Users + Groups).

Authenticated by a per-org SCIM bearer token (see the /scim-tokens admin route). Maps
provider Users -> Third Brain users+memberships and Groups -> teams, scoped to the token's
org. Deactivation suspends the membership (org-scoped), never deletes the global user.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.enums import MembershipStatus, OrgRole
from app.models.sso import ScimToken
from app.models.team import Team, TeamMember
from app.models.user import Membership, User
from app.services.identity import get_membership, provision_user
from app.services.scim import (
    authenticate_scim,
    extract_active,
    group_to_scim,
    list_response,
    user_to_scim,
)

router = APIRouter(prefix="/scim/v2", tags=["scim"])

_SCIM_MEDIA = "application/scim+json"


async def scim_token(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> ScimToken:
    return await authenticate_scim(authorization, db)


def _scim(body: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status_code, media_type=_SCIM_MEDIA)


async def _load_member(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
    user = await db.get(User, user_id)
    membership = await get_membership(db, org_id, user_id) if user else None
    if user is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not provisioned")
    return user, membership


@router.get("/Users")
async def list_users(
    filter: str | None = Query(default=None),
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    stmt = (
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == tok.org_id)
    )
    if filter:
        match = re.match(r'userName eq "(.+)"', filter, re.IGNORECASE)
        if match:
            stmt = stmt.where(User.email == match.group(1).lower())
    rows = (await db.execute(stmt)).all()
    return _scim(list_response([user_to_scim(u, m) for u, m in rows]))


@router.post("/Users")
async def create_user(
    payload: dict,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    email = payload.get("userName") or next(
        (e.get("value") for e in payload.get("emails", []) if e.get("value")), None
    )
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="userName is required")
    name = payload.get("name") or {}
    full = (f"{name.get('givenName', '')} {name.get('familyName', '')}").strip() or payload.get(
        "displayName"
    )
    user, membership, _ = await provision_user(
        db, tok.org_id, email=email, full_name=full, role=OrgRole.VIEWER
    )
    if payload.get("active") is False:
        membership.status = MembershipStatus.SUSPENDED
    await db.commit()
    return _scim(user_to_scim(user, membership), status_code=status.HTTP_201_CREATED)


@router.get("/Users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    user, membership = await _load_member(db, tok.org_id, user_id)
    return _scim(user_to_scim(user, membership))


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    payload: dict,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    user, membership = await _load_member(db, tok.org_id, user_id)
    active = extract_active(payload)
    if active is not None:
        membership.status = MembershipStatus.ACTIVE if active else MembershipStatus.SUSPENDED
    await db.commit()
    return _scim(user_to_scim(user, membership))


@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deprovision_user(
    user_id: uuid.UUID,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    _, membership = await _load_member(db, tok.org_id, user_id)
    membership.status = MembershipStatus.SUSPENDED
    await db.commit()
    return JSONResponse({}, status_code=status.HTTP_204_NO_CONTENT, media_type=_SCIM_MEDIA)


async def _team_member_ids(db: AsyncSession, team_id: uuid.UUID) -> list[str]:
    rows = (
        (await db.execute(select(TeamMember.user_id).where(TeamMember.team_id == team_id)))
        .scalars()
        .all()
    )
    return [str(uid) for uid in rows]


@router.get("/Groups")
async def list_groups(
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    teams = (await db.execute(select(Team).where(Team.org_id == tok.org_id))).scalars().all()
    resources = [group_to_scim(t, await _team_member_ids(db, t.id)) for t in teams]
    return _scim(list_response(resources))


@router.post("/Groups")
async def create_group(
    payload: dict,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    name = payload.get("displayName")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="displayName is required"
        )
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "group"
    team = Team(org_id=tok.org_id, name=name, slug=f"{slug}-{uuid.uuid4().hex[:6]}")
    db.add(team)
    await db.flush()
    for member in payload.get("members", []):
        try:
            uid = uuid.UUID(str(member.get("value")))
        except (ValueError, TypeError):
            continue
        if await get_membership(db, tok.org_id, uid) is not None:
            db.add(TeamMember(team_id=team.id, user_id=uid))
    await db.commit()
    return _scim(
        group_to_scim(team, await _team_member_ids(db, team.id)),
        status_code=status.HTTP_201_CREATED,
    )


@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    tok: ScimToken = Depends(scim_token),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    team = await db.get(Team, group_id)
    if team is None or team.org_id != tok.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    await db.delete(team)
    await db.commit()
    return JSONResponse({}, status_code=status.HTTP_204_NO_CONTENT, media_type=_SCIM_MEDIA)
