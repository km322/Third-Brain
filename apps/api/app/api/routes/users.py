"""Endpoints for the currently authenticated user's own profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.deps import AuthContext, get_session_context
from app.models.organization import Organization
from app.models.user import Membership
from app.schemas.org import CurrentUser, OrgRead
from app.schemas.user import UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=CurrentUser)
async def read_me(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_session_context),
) -> CurrentUser:
    """Return the caller, every organization they belong to and the active one."""
    memberships = (
        (
            await db.execute(
                select(Membership)
                .where(Membership.user_id == ctx.user_id)
                .options(selectinload(Membership.organization))
                .order_by(Membership.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    orgs = [m.organization for m in memberships]
    active = next((o for o in orgs if o.id == ctx.org_id), None)
    if active is None:
        # The token's org is authoritative even if the membership list is stale.
        active = await db.get(Organization, ctx.org_id)
    if active is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Active organization not found"
        )
    return CurrentUser(
        user=UserRead.model_validate(ctx.user),
        organizations=[OrgRead.model_validate(o) for o in orgs],
        active_org=OrgRead.model_validate(active),
        role=ctx.org_role,
    )


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_session_context),
) -> UserRead:
    """Update the caller's own profile (name and/or avatar)."""
    user = ctx.user
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data:
        user.full_name = data["full_name"]
    if "avatar_url" in data:
        user.avatar_url = data["avatar_url"]
    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)
