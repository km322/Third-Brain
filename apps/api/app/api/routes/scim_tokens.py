"""Admin management of SCIM provisioning tokens (session admin auth).

Distinct from the SCIM protocol endpoints (which authenticate WITH these tokens). Only the
SHA-256 hash is stored; the raw token is shown once at creation.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, require_role
from app.core.security import hash_api_key
from app.models.enums import AuditAction, OrgRole
from app.models.sso import ScimToken
from app.schemas.common import Message
from app.schemas.scim import ScimTokenCreate, ScimTokenCreated, ScimTokenRead
from app.services.metering import record_audit

router = APIRouter(prefix="/scim-tokens", tags=["scim"])


@router.get("", response_model=list[ScimTokenRead])
async def list_scim_tokens(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[ScimTokenRead]:
    rows = (
        (
            await db.execute(
                select(ScimToken)
                .where(ScimToken.org_id == ctx.org_id)
                .order_by(ScimToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ScimTokenRead.model_validate(t) for t in rows]


@router.post("", response_model=ScimTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_scim_token(
    payload: ScimTokenCreate,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ScimTokenCreated:
    raw = f"scim_{secrets.token_urlsafe(32)}"
    token = ScimToken(
        org_id=ctx.org_id,
        name=payload.name,
        token_prefix=raw[:12],
        hashed_token=hash_api_key(raw),
        created_by_id=ctx.user_id,
    )
    db.add(token)
    await db.flush()
    await record_audit(
        db,
        ctx,
        AuditAction.SCIM_TOKEN_CREATED.value,
        resource_type="scim_token",
        resource_id=token.id,
    )
    await db.commit()
    await db.refresh(token)
    return ScimTokenCreated(**ScimTokenRead.model_validate(token).model_dump(), token=raw)


@router.delete("/{token_id}", response_model=Message)
async def revoke_scim_token(
    token_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> Message:
    token = await db.get(ScimToken, token_id)
    if token is None or token.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    token.revoked = True
    await db.commit()
    return Message(detail="SCIM token revoked")
