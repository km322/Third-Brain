"""Authentication endpoints: register, login, token refresh, logout and password change.

Sessions are stateless JWT pairs. The **access token** is short-lived and carries the
active organization (``extra={"org": ...}``); the **refresh token** is longer-lived and
user-scoped. Because refresh tokens do not carry an org, the refresh request accepts an
optional ``org_id`` to resume the active org - honored when the user still has an ACTIVE
membership there, otherwise refresh falls back to the user's default org (see
:func:`auth_service.default_org_membership`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import (
    AuthContext,
    client_ip,
    enforce_login_rate_limit,
    ensure_session_not_revoked,
    get_auth_context,
    get_session_context,
)
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import decode_token, hash_password_async, verify_password_async
from app.models.enums import AuditAction, MembershipStatus, OrgRole
from app.models.user import Membership, User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    Tokens,
)
from app.services import auth_service
from app.services.metering import record_audit

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Refresh tokens are single-use: once exchanged (or surrendered at logout) their ``jti``
# is denylisted in Redis until the token's natural expiry.
_REFRESH_DENYLIST_PREFIX = "denylist:refresh:"


async def _consume_refresh_jti(jti: str, exp: object) -> bool:
    """Atomically claim a refresh token id for single use.

    Returns True when this call is the first to consume ``jti`` (refresh may proceed) and
    False when it was already used or revoked (caller must 401). A single Redis ``SET NX``
    makes it atomic, so two concurrent exchanges of the same token cannot both succeed.
    Fails OPEN on a Redis outage - the denylist backend being down must not lock users out
    of refreshing - so single-use enforcement lapses only for the duration of an outage.
    """
    try:
        ttl = int(exp) - int(datetime.now(UTC).timestamp())
    except (TypeError, ValueError):
        return True
    if ttl <= 0:
        return True
    try:
        redis = get_redis()
        claimed = await redis.set(f"{_REFRESH_DENYLIST_PREFIX}{jti}", "1", ex=ttl, nx=True)
        return bool(claimed)
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Refresh denylist unavailable (%s); allowing single use", exc)
        return True


async def _denylist_refresh_jti(jti: str, exp: object) -> None:
    """Revoke a refresh token id until its natural expiry (best-effort, fails open)."""
    try:
        ttl = int(exp) - int(datetime.now(UTC).timestamp())
    except (TypeError, ValueError):
        return
    if ttl <= 0:
        return
    try:
        redis = get_redis()
        await redis.set(f"{_REFRESH_DENYLIST_PREFIX}{jti}", "1", ex=ttl)
    except Exception as exc:  # pragma: no cover - Redis outage path
        logger.warning("Refresh denylist unavailable (%s); token not revoked", exc)


@router.post("/register", response_model=Tokens, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """Create a new user, bootstrap their first organization (as OWNER) and sign in.

    Closed by default in production: a brand-new org has no connector of its own, so its
    requests are served by the deployment's platform provider keys. Leaving self-serve
    signup open on a keyed deployment lets anyone bill the operator, so it must be opted
    into explicitly via ``SIGNUP_ENABLED``.
    """
    if not settings.signup_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-serve signup is closed. Please request access.",
        )
    await enforce_login_rate_limit(request, payload.email)
    user, org = await auth_service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        org_name=payload.org_name,
    )
    user.last_login_at = datetime.now(UTC)
    ctx = AuthContext(org_id=org.id, org_role=OrgRole.OWNER, user=user)
    await record_audit(
        db,
        ctx,
        AuditAction.USER_LOGIN.value,
        resource_type="user",
        resource_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta={"via": "register"},
    )
    await db.commit()
    return auth_service.make_tokens(user.id, org.id, user.token_version)


@router.post("/login", response_model=Tokens)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """Verify credentials and return a token pair scoped to the user's default org."""
    await enforce_login_rate_limit(request, payload.email)
    user = await auth_service.authenticate_user(db, payload.email, payload.password)
    membership = await auth_service.default_org_membership(db, user.id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not a member of any organization",
        )
    user.last_login_at = datetime.now(UTC)
    ctx = AuthContext(org_id=membership.org_id, org_role=membership.role, user=user)
    await record_audit(
        db,
        ctx,
        AuditAction.USER_LOGIN.value,
        resource_type="user",
        resource_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return auth_service.make_tokens(user.id, membership.org_id, user.token_version)


@router.post("/refresh", response_model=Tokens)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """Exchange a valid refresh token for a fresh token pair.

    Refresh tokens are single-use: the presented token's ``jti`` is denylisted on success,
    so replaying it (e.g. after theft) yields 401. Resumes into the org given by
    ``payload.org_id`` when the user has an ACTIVE membership there; otherwise falls back
    to the user's default organization.
    """
    # Key the limiter on the WHOLE token (enforce_login_rate_limit hashes the identifier
    # internally): every HS256 JWT shares the same header prefix, so keying on a slice
    # would collapse all users behind one IP (corporate NAT) into a single bucket.
    await enforce_login_rate_limit(request, payload.refresh_token)
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token")
    try:
        user_id = uuid.UUID(str(claims.get("sub")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token subject"
        ) from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    ensure_session_not_revoked(claims, user)
    jti = claims.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used or revoked",
        )

    # Preserve the caller's active org across refresh when they still have an ACTIVE
    # membership there; otherwise resume into their default org. Without this, a silent
    # refresh would silently drop the user back into their default org mid-session.
    membership = None
    if payload.org_id is not None:
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.org_id == payload.org_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
    if membership is None:
        membership = await auth_service.default_org_membership(db, user.id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not a member of any organization",
        )
    # Single-use: atomically claim the jti now. A replay - or a concurrent duplicate of
    # the same token - loses the claim and is rejected, so one refresh token can never
    # mint two valid pairs.
    if not await _consume_refresh_jti(jti, claims.get("exp")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used or revoked",
        )
    return auth_service.make_tokens(user.id, membership.org_id, user.token_version)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    payload: LogoutRequest | None = None,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Record the logout event; the client discards its (stateless) access token.

    When the optional body surrenders a refresh token belonging to this user, its
    ``jti`` is denylisted so the token cannot be replayed later. The body stays
    optional so older clients that send none keep working.
    """
    if payload is not None and payload.refresh_token:
        try:
            claims = decode_token(payload.refresh_token)
        except jwt.PyJWTError:
            claims = None
        if (
            claims is not None
            and claims.get("type") == "refresh"
            and claims.get("sub") == str(ctx.user_id)
            and claims.get("jti")
        ):
            await _denylist_refresh_jti(claims["jti"], claims.get("exp"))
    await record_audit(
        db,
        ctx,
        AuditAction.USER_LOGOUT.value,
        resource_type="user",
        resource_id=ctx.user_id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", response_model=Tokens)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    ctx: AuthContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """Change the caller's password after verifying the current one (session only).

    Bumps ``token_version`` so every outstanding session - access and refresh alike -
    is revoked, then returns a fresh pair minted with the new version so this caller
    stays signed in. API keys are rejected: a key must never rotate a human's password.
    """
    user = ctx.user
    await enforce_login_rate_limit(request, user.email)
    if not await verify_password_async(payload.current_password, user.hashed_password or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    user.hashed_password = await hash_password_async(payload.new_password)
    new_version = await auth_service.bump_token_version(db, user.id)
    await record_audit(
        db,
        ctx,
        AuditAction.USER_PASSWORD_CHANGED.value,
        resource_type="user",
        resource_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return auth_service.make_tokens(user.id, ctx.org_id, new_version)
