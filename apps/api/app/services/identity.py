"""Shared user/membership provisioning for invites, SSO and SCIM.

One place that turns an email (+ optional name/password/role) into a Third Brain user and
org membership, so the three provisioning paths stay consistent. Everything is org-scoped.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password_async
from app.models.enums import MembershipStatus, OrgRole
from app.models.user import Membership, User


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(func.lower(User.email) == email.strip().lower()))
    ).scalar_one_or_none()


async def get_membership(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    return (
        await db.execute(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()


async def provision_user(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    email: str,
    full_name: str | None = None,
    role: OrgRole = OrgRole.VIEWER,
    password: str | None = None,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> tuple[User, Membership, bool]:
    """Find-or-create a user by email and ensure an org membership.

    Returns ``(user, membership, created_user)``. An existing user is reused (their name
    is filled in only if missing); an existing membership is reused (never downgraded).
    The caller commits.
    """
    user = await get_user_by_email(db, email)
    created = False
    if user is None:
        user = User(
            email=email.strip().lower(),
            full_name=full_name,
            hashed_password=(await hash_password_async(password)) if password else None,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        created = True
    elif full_name and not user.full_name:
        user.full_name = full_name

    membership = await get_membership(db, org_id, user.id)
    if membership is None:
        membership = Membership(org_id=org_id, user_id=user.id, role=role, status=status)
        db.add(membership)
        await db.flush()
    return user, membership, created
