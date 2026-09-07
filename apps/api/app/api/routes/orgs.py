"""Organization endpoints: listing, creation, switching the active org, and
membership administration.

Membership administration (list/invite/update/remove) requires org ADMIN or higher,
enforced via :func:`app.core.deps.require_role`. Owner-level changes are further
restricted to owners, and the last owner of an org can never be demoted or removed.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.deps import (
    AuthContext,
    client_ip,
    get_auth_context,
    get_session_context,
    require_role,
)
from app.core.security import hash_password_async
from app.models.access import AccessGrant
from app.models.api_key import ApiKey
from app.models.collection import Collection
from app.models.enums import AuditAction, MembershipStatus, OrgRole, PrincipalType
from app.models.organization import Organization
from app.models.team import Team, TeamMember
from app.models.user import Membership
from app.schemas.auth import Tokens
from app.schemas.org import (
    MemberInvite,
    MemberPasswordReset,
    MembershipRead,
    MemberUpdate,
    OrgCreate,
    OrgRead,
    OrgSwitch,
    OrgUpdate,
)
from app.services import auth_service
from app.services.metering import record_audit

router = APIRouter(prefix="/orgs", tags=["orgs"])


# --------------------------------------------------------------------------- #
# Organizations
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[OrgRead])
async def list_orgs(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_session_context),
) -> list[Organization]:
    """List every organization the current user belongs to."""
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
    return [m.organization for m in memberships]


@router.post("", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_org(
    payload: OrgCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_session_context),
) -> Organization:
    """Create a new organization; the caller becomes its OWNER."""
    org, _ = await auth_service.create_org_with_owner(db, payload.name, ctx.user)
    await db.commit()
    await db.refresh(org)
    return org


@router.post("/switch", response_model=Tokens)
async def switch_org(
    payload: OrgSwitch,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_session_context),
) -> Tokens:
    """Issue a new token pair scoped to a different organization the user belongs to."""
    membership = await auth_service.get_membership(db, ctx.user_id, payload.org_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of that organization",
        )
    if membership.status != MembershipStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your membership of that organization is not active",
        )
    return auth_service.make_tokens(ctx.user_id, payload.org_id, ctx.user.token_version)


@router.get("/current", response_model=OrgRead)
async def get_current_org(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Organization:
    """Return the organization the current session is scoped to."""
    org = await db.get(Organization, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.patch("/current", response_model=OrgRead)
async def update_current_org(
    payload: OrgUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
) -> Organization:
    """Update the active organization's name and/or settings (admin only)."""
    org = await db.get(Organization, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        org.name = data["name"].strip()
    if data.get("settings") is not None:
        # Shallow-merge so callers can patch individual keys without losing the rest.
        org.settings = {**(org.settings or {}), **data["settings"]}
    await db.commit()
    await db.refresh(org)
    return org


# --------------------------------------------------------------------------- #
# Members (admin only)
# --------------------------------------------------------------------------- #
async def _load_member(db: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID) -> Membership:
    membership = (
        await db.execute(
            select(Membership)
            .where(Membership.id == membership_id, Membership.org_id == org_id)
            .options(selectinload(Membership.user))
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    return membership


@router.get("/members", response_model=list[MembershipRead])
async def list_members(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
) -> list[Membership]:
    """List the members of the active organization, each with their user record."""
    return (
        (
            await db.execute(
                select(Membership)
                .where(Membership.org_id == ctx.org_id)
                .options(selectinload(Membership.user))
                .order_by(Membership.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/members/invite",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    payload: MemberInvite,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
) -> MembershipRead:
    """Add an existing user to the active organization.

    Without an email-delivery pipeline, the invitee must already have a Third Brain
    account; the membership is created in the ``INVITED`` state and an admin can move
    it to ``ACTIVE`` via ``PATCH /orgs/members/{id}``.
    """
    if payload.role == OrgRole.OWNER and ctx.org_role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can grant the owner role",
        )
    user = await auth_service.get_user_by_email(db, payload.email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user with that email address exists; ask them to sign up first",
        )
    if await auth_service.get_membership(db, user.id, ctx.org_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user is already a member of the organization",
        )
    membership = Membership(
        org_id=ctx.org_id,
        user_id=user.id,
        role=payload.role,
        status=MembershipStatus.INVITED,
        invited_by_id=ctx.user_id,
    )
    db.add(membership)
    await db.commit()
    # Do NOT embed the invitee's user profile in the response. This endpoint accepts an
    # arbitrary email and is reachable by any org admin (which any self-registered user can
    # become), so returning name/avatar/last_login/created_at would leak a cross-tenant user's
    # profile and make the route a PII-harvesting oracle. The membership fields are enough for
    # the caller; the members list (own-org only) still shows full profiles.
    return MembershipRead(
        id=membership.id,
        org_id=membership.org_id,
        user_id=membership.user_id,
        role=membership.role,
        status=membership.status,
        user=None,
    )


@router.patch("/members/{membership_id}", response_model=MembershipRead)
async def update_member(
    membership_id: uuid.UUID,
    payload: MemberUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
) -> Membership:
    """Change a member's role and/or status (admin only)."""
    membership = await _load_member(db, ctx.org_id, membership_id)
    data = payload.model_dump(exclude_unset=True)

    new_role = data.get("role")
    if new_role is not None and new_role != membership.role:
        # Owner-level transitions (in either direction) are owner-only.
        if OrgRole.OWNER in (new_role, membership.role) and ctx.org_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an owner can change owner-level roles",
            )
        # Never leave an org without an owner.
        if (
            membership.role == OrgRole.OWNER
            and new_role != OrgRole.OWNER
            and (
                await auth_service.count_owners(db, ctx.org_id, exclude_membership_id=membership.id)
                == 0
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last owner of the organization",
            )
        membership.role = new_role

    new_status = data.get("status")
    if new_status is not None:
        # Changing an owner's status (in either direction) is owner-only.
        if membership.role == OrgRole.OWNER and ctx.org_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an owner can change an owner's status",
            )
        # Don't let an org suspend/deactivate its last remaining owner (which would
        # lock everyone out - a suspended owner can't switch into the org).
        if (
            membership.role == OrgRole.OWNER
            and new_status != MembershipStatus.ACTIVE
            and (
                await auth_service.count_owners(db, ctx.org_id, exclude_membership_id=membership.id)
                == 0
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot suspend the last owner of the organization",
            )
        membership.status = new_status

    await db.commit()
    return membership


@router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    membership_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
) -> Response:
    """Remove a member from the active organization (admin only)."""
    membership = await _load_member(db, ctx.org_id, membership_id)
    if membership.user_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove yourself from the organization",
        )
    if membership.role == OrgRole.OWNER:
        if ctx.org_role != OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an owner can remove another owner",
            )
        if (
            await auth_service.count_owners(db, ctx.org_id, exclude_membership_id=membership.id)
            == 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner of the organization",
            )
    # Purge the user's ACL grants and team memberships in this org. AccessGrant.principal_id
    # and TeamMember.user_id have no FK to the membership, so without this a later re-invite
    # of the same user would silently restore every grant/team-derived permission they once
    # held - access no admin re-granted.
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.org_id == ctx.org_id,
            AccessGrant.principal_type == PrincipalType.USER,
            AccessGrant.principal_id == membership.user_id,
        )
    )
    await db.execute(
        delete(TeamMember).where(
            TeamMember.user_id == membership.user_id,
            TeamMember.team_id.in_(select(Team.id).where(Team.org_id == ctx.org_id)),
        )
    )
    # Collection ownership has no FK to the membership either; clear it so a removed owner
    # keeps no implicit MANAGER on collections they owned (which a later re-invite of the
    # same user would otherwise silently restore).
    await db.execute(
        update(Collection)
        .where(Collection.org_id == ctx.org_id, Collection.owner_id == membership.user_id)
        .values(owner_id=None)
    )
    # Revoke API keys the removed user created OR that impersonate them, scoped to this org.
    # These keys carry the org's access independently of the membership row, so without this
    # an offboarded admin keeps live programmatic access indefinitely (a non-acts_as key does
    # not even depend on the deleted user still being active).
    await db.execute(
        update(ApiKey)
        .where(
            ApiKey.org_id == ctx.org_id,
            or_(
                ApiKey.created_by_id == membership.user_id,
                ApiKey.acts_as_user_id == membership.user_id,
            ),
        )
        .values(revoked=True)
    )
    await db.delete(membership)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/members/{membership_id}/reset-password", response_model=MemberPasswordReset)
async def reset_member_password(
    membership_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    _session: AuthContext = Depends(get_session_context),
) -> MemberPasswordReset:
    """Reset a member's password to a one-time temporary value (admin session only).

    The temporary password is returned exactly once and only its bcrypt hash is stored.
    The target's ``token_version`` is bumped, revoking every session they had. Guards:
    only an owner may reset an owner; you cannot reset yourself (use
    ``/auth/change-password``); and a target holding a membership in any other
    organization (active, invited or suspended) is refused (409) - the reset sets a
    GLOBAL password an admin could later use against that other tenant once the
    membership there is active, so such users must be reset from their own account or
    through support.
    """
    membership = await _load_member(db, ctx.org_id, membership_id)
    if membership.user_id == ctx.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot reset your own password; use /auth/change-password",
        )
    if membership.role == OrgRole.OWNER and ctx.org_role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can reset an owner's password",
        )
    other_memberships = (
        await db.execute(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.user_id == membership.user_id,
                Membership.org_id != ctx.org_id,
            )
        )
    ).scalar_one()
    if other_memberships:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This user belongs to other organizations; they must reset their password "
                "from their own account or through support"
            ),
        )
    temporary_password = secrets.token_urlsafe(12)
    user = membership.user
    user.hashed_password = await hash_password_async(temporary_password)
    await auth_service.bump_token_version(db, user.id)
    await record_audit(
        db,
        ctx,
        AuditAction.USER_PASSWORD_RESET.value,
        resource_type="user",
        resource_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return MemberPasswordReset(temporary_password=temporary_password)
