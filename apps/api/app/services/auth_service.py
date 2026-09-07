"""Authentication and organization-bootstrap logic.

Keeps the HTTP layer thin: routes validate input and commit; these helpers own the
domain rules (slugging, uniqueness, password checks, token minting). Nothing here
commits - the calling request owns the transaction boundary.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_password_async,
    verify_password,
)
from app.models.enums import MembershipStatus, OrgRole
from app.models.organization import Organization
from app.models.user import Membership, User
from app.schemas.auth import Tokens

_NON_SLUG = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# Slugs
# --------------------------------------------------------------------------- #
def slugify(value: str) -> str:
    """Turn an arbitrary name into a URL-safe slug (ascii, lowercase, hyphenated)."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", normalized.lower()).strip("-")
    return slug or "org"


async def unique_org_slug(db: AsyncSession, name: str) -> str:
    """Return a slug derived from ``name`` that no organization currently uses.

    Collisions are resolved with a numeric suffix: ``acme`` → ``acme-2`` → ``acme-3``.
    """
    base = slugify(name)
    candidate = base
    suffix = 1
    while (
        await db.execute(select(Organization.id).where(Organization.slug == candidate))
    ).first() is not None:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def make_tokens(user_id: uuid.UUID, org_id: uuid.UUID, token_version: int) -> Tokens:
    """Mint an access token bound to ``org_id`` plus a user-scoped refresh token.

    Both tokens embed the user's current ``token_version`` as ``ver`` (re-checked on
    every request, so bumping the version revokes all outstanding sessions). The
    refresh token also carries a unique ``jti`` so it can be denylisted after use
    (rotation) or on logout.
    """
    access = create_access_token(
        subject=str(user_id), extra={"org": str(org_id), "ver": token_version}
    )
    refresh = create_refresh_token(
        subject=str(user_id), extra={"ver": token_version, "jti": uuid.uuid4().hex}
    )
    return Tokens(access_token=access, refresh_token=refresh, token_type="bearer")


async def bump_token_version(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Atomically increment a user's ``token_version`` and return the new value.

    Done as a single ``UPDATE ... RETURNING`` (not a read-modify-write on the ORM object)
    so two concurrent credential events - e.g. an admin reset racing the user's own
    password change - cannot lost-update and leave a just-minted token still valid.
    Revokes every outstanding access and refresh token for the user. Does not commit.
    """
    return (
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(token_version=User.token_version + 1)
            .returning(User.token_version)
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Case-insensitive lookup of a user by email address."""
    return (
        await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()


async def get_membership(
    db: AsyncSession, user_id: uuid.UUID | None, org_id: uuid.UUID
) -> Membership | None:
    if user_id is None:
        return None
    return (
        await db.execute(
            select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
        )
    ).scalar_one_or_none()


async def default_org_membership(db: AsyncSession, user_id: uuid.UUID) -> Membership | None:
    """Choose which org a session should resume into.

    Returns the oldest ``ACTIVE`` membership, or ``None`` when the user has none. Used by
    login/refresh; resuming into a suspended/invited membership must not grant access (the
    request layer would reject it anyway), so those are never chosen.
    """
    return (
        (
            await db.execute(
                select(Membership)
                .where(
                    Membership.user_id == user_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
                .order_by(Membership.created_at.asc())
            )
        )
        .scalars()
        .first()
    )


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
async def create_org_with_owner(
    db: AsyncSession, name: str, owner: User
) -> tuple[Organization, Membership]:
    """Create an organization and an ``OWNER`` membership for ``owner``.

    Flushes so ids are available; does not commit.
    """
    org = Organization(name=name.strip(), slug=await unique_org_slug(db, name))
    db.add(org)
    await db.flush()
    membership = Membership(
        org_id=org.id,
        user_id=owner.id,
        role=OrgRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    db.add(membership)
    await db.flush()
    return org, membership


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    org_name: str,
) -> tuple[User, Organization]:
    """Create a new user plus their first organization (as ``OWNER``).

    Raises ``409`` if the email is already registered. Does not commit.
    """
    if await get_user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
        )
    user = User(
        email=email.lower(),
        hashed_password=await hash_password_async(password),
        full_name=full_name.strip(),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    org, _ = await create_org_with_owner(db, org_name, user)
    return user, org


# Precomputed bcrypt hash of a throwaway password. Authenticating an unknown email
# (or a user with no stored password) still verifies against this hash so the bcrypt
# cost is paid every time; without it the 401 comes back measurably faster for
# non-existent accounts and leaks which emails are registered.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-equalization")


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Verify credentials and return the user, or raise ``401``/``403``.

    A generic 401 is returned for both unknown emails and bad passwords so the
    endpoint does not reveal which accounts exist. When no matching user (or stored
    password) is found the supplied password is still verified against a dummy hash,
    so the bcrypt cost is paid regardless and response timing cannot reveal which
    emails exist.
    """
    user = await get_user_by_email(db, email)
    if user is None or not user.hashed_password:
        await asyncio.to_thread(verify_password, password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not await asyncio.to_thread(verify_password, password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )
    return user


async def count_owners(
    db: AsyncSession, org_id: uuid.UUID, exclude_membership_id: uuid.UUID | None = None
) -> int:
    """Count ACTIVE ``OWNER`` memberships in an org, optionally excluding one membership.

    Only active owners count toward the "an org must always keep an owner" guard: a suspended
    or still-invited owner cannot sign in, so counting them would let the last *active* owner
    demote/suspend themselves and lock everyone out of the organization.
    """
    query = (
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.org_id == org_id,
            Membership.role == OrgRole.OWNER,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    if exclude_membership_id is not None:
        query = query.where(Membership.id != exclude_membership_id)
    return (await db.execute(query)).scalar_one()
