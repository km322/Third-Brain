"""API-key management.

Programmatic credentials are minted here. The raw secret is generated with
``generate_api_key()`` and returned exactly once; only the prefix and a SHA-256 hash
are persisted. Key management is a sensitive, org-wide operation, so every endpoint
requires an **admin user session** - API keys cannot mint or revoke other keys, which
closes an impersonation/privilege-escalation path via ``acts_as_user_id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, get_session_context, role_at_least
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.enums import AuditAction
from app.models.user import Membership
from app.schemas.api_key import (
    ALLOWED_SCOPES,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
)
from app.services.metering import record_audit

router = APIRouter(prefix="/api-keys", tags=["api_keys"])


async def require_admin_session(
    ctx: AuthContext = Depends(get_session_context),
) -> AuthContext:
    """Only admin/owner human sessions may manage API keys (device-auth reuses this)."""
    if not ctx.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires org admin role",
        )
    return ctx


async def validate_acts_as_user(
    db: AsyncSession, ctx: AuthContext, acts_as_user_id: uuid.UUID
) -> None:
    """Guard the ``acts_as_user_id`` binding on a key being minted.

    The impersonated user must be an org member, and (to prevent privilege escalation)
    may not outrank the admin minting the key - otherwise an admin could mint an
    owner-acting key and self-promote. Shared with the device-auth approval flow.
    """
    member = (
        await db.execute(
            select(Membership).where(
                Membership.org_id == ctx.org_id,
                Membership.user_id == acts_as_user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=400,
            detail="acts_as_user_id must reference a member of this organization",
        )
    if not role_at_least(ctx.org_role, member.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create an API key acting as a user whose role is higher than yours",
        )


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


async def _get_key(db: AsyncSession, ctx: AuthContext, key_id: uuid.UUID) -> ApiKey:
    key = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return key


@router.get("", response_model=list[ApiKeyRead])
async def list_api_keys(
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    """List the organization's API keys (secrets are never returned)."""
    keys = (
        (
            await db.execute(
                select(ApiKey).where(ApiKey.org_id == ctx.org_id).order_by(ApiKey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(keys)


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    """Mint a new API key. The raw secret is returned once and never stored."""
    unknown = [s for s in payload.scopes if s not in ALLOWED_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scope(s): {', '.join(sorted(set(unknown)))}. "
            f"Allowed: {', '.join(sorted(ALLOWED_SCOPES))}",
        )

    if payload.expires_at is not None:
        expires = payload.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise HTTPException(status_code=400, detail="expires_at must be in the future")

    if payload.acts_as_user_id is not None:
        await validate_acts_as_user(db, ctx, payload.acts_as_user_id)

    full_key, prefix, hashed = generate_api_key()
    key = ApiKey(
        org_id=ctx.org_id,
        created_by_id=ctx.user_id,
        acts_as_user_id=payload.acts_as_user_id,
        name=payload.name,
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=list(payload.scopes),
        rate_limit_per_minute=(
            payload.rate_limit_per_minute
            if payload.rate_limit_per_minute is not None
            else settings.DEFAULT_RATE_LIMIT_PER_MINUTE
        ),
        expires_at=payload.expires_at,
    )
    db.add(key)
    await db.flush()

    ip, ua = _client_meta(request)
    await record_audit(
        db,
        ctx,
        AuditAction.API_KEY_CREATED.value,
        resource_type="api_key",
        resource_id=key.id,
        ip_address=ip,
        user_agent=ua,
        meta={"name": key.name, "scopes": key.scopes},
    )
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated(api_key=ApiKeyRead.model_validate(key), secret=full_key)


@router.post("/{key_id}/revoke", response_model=ApiKeyRead)
async def revoke_api_key(
    key_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    """Revoke a key without deleting it (keeps its audit/usage history intact)."""
    key = await _get_key(db, ctx, key_id)
    if not key.revoked:
        key.revoked = True
        ip, ua = _client_meta(request)
        await record_audit(
            db,
            ctx,
            AuditAction.API_KEY_REVOKED.value,
            resource_type="api_key",
            resource_id=key.id,
            ip_address=ip,
            user_agent=ua,
            meta={"name": key.name},
        )
        await db.commit()
        await db.refresh(key)
    return key


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Permanently delete a key."""
    key = await _get_key(db, ctx, key_id)
    ip, ua = _client_meta(request)
    await record_audit(
        db,
        ctx,
        AuditAction.API_KEY_REVOKED.value,
        resource_type="api_key",
        resource_id=key.id,
        ip_address=ip,
        user_agent=ua,
        meta={"name": key.name, "deleted": True},
    )
    await db.delete(key)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
