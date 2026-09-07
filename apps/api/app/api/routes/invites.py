"""Email-based org invitations (replaces the old "invitee must already exist" flow).

An admin invites an email; a tokenised link is emailed; the recipient accepts by choosing a
name + password, which provisions their account and activates their membership. Accepting
only ever creates a NEW user - if the email already has an account, the admin adds them
through the existing members flow instead (so an invite token can never sign in as an
existing account without its password).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, client_ip, enforce_login_rate_limit, require_role
from app.core.security import hash_api_key
from app.models.enums import AuditAction, InviteStatus, MembershipStatus, OrgRole
from app.models.organization import Organization
from app.models.sso import Invite
from app.schemas.auth import Tokens
from app.schemas.common import Message
from app.schemas.invite import InviteAccept, InviteCreate, InviteRead
from app.services import auth_service
from app.services.email import send_email
from app.services.identity import get_user_by_email, provision_user
from app.services.metering import record_audit

router = APIRouter(prefix="/invites", tags=["invites"])


@router.post("", response_model=InviteRead, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreate,
    request: Request,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InviteRead:
    """Invite an email to the org.

    Emails a tokenised acceptance link AND returns it as ``accept_url``. The token is stored
    only as a hash, so this response is the one and only chance to see the link - which
    matters because the default ``EMAIL_PROVIDER=stub`` delivers nothing, so on a self-host
    that has not configured SMTP the admin must pass the link to the invitee themselves.
    """
    # An admin must not be able to mint an OWNER via an email invite (privilege escalation);
    # only an owner can grant the owner role - parity with POST /orgs/members/invite.
    if payload.role == OrgRole.OWNER and ctx.org_role != OrgRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can invite a new owner",
        )
    email = payload.email.lower()
    if await get_user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists; add them from Members instead.",
        )

    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.INVITE_EXPIRE_HOURS)
    invite = (
        await db.execute(select(Invite).where(Invite.org_id == ctx.org_id, Invite.email == email))
    ).scalar_one_or_none()
    if invite is None:
        invite = Invite(org_id=ctx.org_id, email=email)
        db.add(invite)
    invite.role = payload.role
    invite.token_prefix = token[:12]
    invite.hashed_token = hash_api_key(token)
    invite.invited_by_id = ctx.user_id
    invite.status = InviteStatus.PENDING
    invite.expires_at = expires
    invite.accepted_at = None
    await db.flush()

    org = await db.get(Organization, ctx.org_id)
    accept_url = f"{settings.APP_BASE_URL}/accept-invite?token={token}"
    await send_email(
        email,
        f"You're invited to {org.name} on Third Brain",
        f"You've been invited to join {org.name} on Third Brain as a {payload.role.value}.\n\n"
        f"Accept your invitation:\n{accept_url}\n\n"
        f"This link expires in {settings.INVITE_EXPIRE_HOURS} hours.",
    )
    await record_audit(
        db,
        ctx,
        AuditAction.MEMBER_INVITED.value,
        resource_type="invite",
        resource_id=invite.id,
        ip_address=client_ip(request),
        meta={"email": email, "role": payload.role.value},
    )
    await db.commit()
    await db.refresh(invite)
    return InviteRead.model_validate(invite).model_copy(update={"accept_url": accept_url})


@router.get("", response_model=list[InviteRead])
async def list_invites(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[InviteRead]:
    rows = (
        (
            await db.execute(
                select(Invite)
                .where(Invite.org_id == ctx.org_id, Invite.status == InviteStatus.PENDING)
                .order_by(Invite.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [InviteRead.model_validate(i) for i in rows]


@router.delete("/{invite_id}", response_model=Message)
async def revoke_invite(
    invite_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> Message:
    invite = await db.get(Invite, invite_id)
    if invite is None or invite.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    invite.status = InviteStatus.REVOKED
    await record_audit(
        db, ctx, AuditAction.INVITE_REVOKED.value, resource_type="invite", resource_id=invite.id
    )
    await db.commit()
    return Message(detail="Invite revoked")


@router.post("/accept", response_model=Tokens)
async def accept_invite(
    payload: InviteAccept,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """Accept an invitation: provision the new account, activate membership, sign in."""
    await enforce_login_rate_limit(request, payload.token)
    invite = (
        await db.execute(select(Invite).where(Invite.hashed_token == hash_api_key(payload.token)))
    ).scalar_one_or_none()
    if invite is None or invite.status != InviteStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or already-used invitation"
        )
    if invite.expires_at < datetime.now(UTC):
        invite.status = InviteStatus.EXPIRED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation has expired"
        )

    # Close the invite->account race: the email had no account when the invite was created
    # (create_invite 409s otherwise), but one may have been registered since. Accepting must
    # only ever provision a NEW account - never adopt a pre-existing one - or the token holder
    # would get a session for that account without its password (account takeover).
    if await get_user_by_email(db, invite.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An account with this email already exists. Sign in instead; an admin can "
                "add you to the organization from Members."
            ),
        )

    user, membership, created = await provision_user(
        db,
        invite.org_id,
        email=invite.email,
        full_name=payload.full_name,
        role=invite.role,
        password=payload.password,
        status=MembershipStatus.ACTIVE,
    )
    if not created:  # pragma: no cover - defense in depth behind the pre-check above
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    membership.status = MembershipStatus.ACTIVE
    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = datetime.now(UTC)
    user.last_login_at = datetime.now(UTC)
    ctx = AuthContext(org_id=invite.org_id, org_role=membership.role, user=user)
    await record_audit(
        db,
        ctx,
        AuditAction.INVITE_ACCEPTED.value,
        resource_type="invite",
        resource_id=invite.id,
        ip_address=client_ip(request),
        meta={"email": invite.email},
    )
    await db.commit()
    return auth_service.make_tokens(user.id, invite.org_id, user.token_version)
