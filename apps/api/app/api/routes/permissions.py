"""Access-grant (ACL) management for collections and documents.

Grants are explicit ``principal -> resource -> permission`` rows layered on top of the
visibility/ownership baseline computed by :mod:`app.services.permissions`. Managing a
resource's grants requires ``manager`` permission on that resource (org admins qualify
automatically). Anyone may query their own *effective* permission.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context
from app.models.access import AccessGrant
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    AuditAction,
    PermissionLevel,
    PrincipalType,
    ResourceType,
)
from app.models.team import Team
from app.models.user import Membership, User
from app.schemas.permission import (
    AccessGrantCreate,
    AccessGrantRead,
    EffectivePermissionRead,
)
from app.services.metering import record_audit
from app.services.permissions import effective_permission, require_permission

router = APIRouter(prefix="/permissions", tags=["permissions"])


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


async def _require_resource_in_org(
    db: AsyncSession,
    ctx: AuthContext,
    resource_type: ResourceType,
    resource_id: uuid.UUID,
) -> None:
    """404 if the target resource does not exist within the caller's org."""
    model = Collection if resource_type == ResourceType.COLLECTION else Document
    obj = await db.get(model, resource_id)
    if obj is None or obj.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail=f"{resource_type.value.capitalize()} not found")


async def _principal_names(
    db: AsyncSession, ctx: AuthContext, grants: list[AccessGrant]
) -> dict[uuid.UUID, str]:
    """Resolve display names for the principals referenced by ``grants``."""
    user_ids = {g.principal_id for g in grants if g.principal_type == PrincipalType.USER}
    team_ids = {g.principal_id for g in grants if g.principal_type == PrincipalType.TEAM}
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        for u in users:
            names[u.id] = u.full_name or u.email
    if team_ids:
        teams = (
            (await db.execute(select(Team).where(Team.id.in_(team_ids), Team.org_id == ctx.org_id)))
            .scalars()
            .all()
        )
        for t in teams:
            names[t.id] = t.name
    return names


def _to_read(grant: AccessGrant, name: str | None) -> AccessGrantRead:
    return AccessGrantRead(
        id=grant.id,
        resource_type=grant.resource_type,
        resource_id=grant.resource_id,
        principal_type=grant.principal_type,
        principal_id=grant.principal_id,
        permission=grant.permission,
        principal_name=name,
    )


@router.get("", response_model=list[AccessGrantRead])
async def list_grants(
    resource_type: ResourceType = Query(...),
    resource_id: uuid.UUID = Query(...),
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[AccessGrantRead]:
    """List every explicit grant on a resource. Requires ``manager`` on it."""
    await _require_resource_in_org(db, ctx, resource_type, resource_id)
    await require_permission(db, ctx, resource_type, resource_id, PermissionLevel.MANAGER)

    grants = (
        (
            await db.execute(
                select(AccessGrant)
                .where(
                    AccessGrant.org_id == ctx.org_id,
                    AccessGrant.resource_type == resource_type,
                    AccessGrant.resource_id == resource_id,
                )
                .order_by(AccessGrant.created_at)
            )
        )
        .scalars()
        .all()
    )
    names = await _principal_names(db, ctx, list(grants))
    return [_to_read(g, names.get(g.principal_id)) for g in grants]


@router.get("/effective", response_model=EffectivePermissionRead)
async def get_effective_permission(
    resource_type: ResourceType = Query(...),
    resource_id: uuid.UUID = Query(...),
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> EffectivePermissionRead:
    """Return the caller's own effective permission on a resource."""
    level = await effective_permission(db, ctx, resource_type, resource_id)
    return EffectivePermissionRead(permission=level)


@router.post("", response_model=AccessGrantRead, status_code=status.HTTP_201_CREATED)
async def upsert_grant(
    payload: AccessGrantCreate,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AccessGrantRead:
    """Create or update a grant (upsert on the unique principal/resource tuple).

    The principal must belong to the caller's organization.
    """
    if payload.permission == PermissionLevel.NONE:
        raise HTTPException(
            status_code=400,
            detail="Cannot grant 'none'; delete the grant to revoke access instead",
        )

    await _require_resource_in_org(db, ctx, payload.resource_type, payload.resource_id)
    await require_permission(
        db, ctx, payload.resource_type, payload.resource_id, PermissionLevel.MANAGER
    )

    name = await _validate_principal(db, ctx, payload.principal_type, payload.principal_id)

    existing = (
        await db.execute(
            select(AccessGrant).where(
                and_(
                    AccessGrant.resource_type == payload.resource_type,
                    AccessGrant.resource_id == payload.resource_id,
                    AccessGrant.principal_type == payload.principal_type,
                    AccessGrant.principal_id == payload.principal_id,
                )
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.permission = payload.permission
        existing.granted_by_id = ctx.user_id
        grant = existing
    else:
        grant = AccessGrant(
            org_id=ctx.org_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            permission=payload.permission,
            granted_by_id=ctx.user_id,
        )
        db.add(grant)

    await db.flush()
    ip, ua = _client_meta(request)
    await record_audit(
        db,
        ctx,
        AuditAction.PERMISSION_GRANTED.value,
        resource_type=payload.resource_type.value,
        resource_id=payload.resource_id,
        ip_address=ip,
        user_agent=ua,
        meta={
            "principal_type": payload.principal_type.value,
            "principal_id": str(payload.principal_id),
            "permission": payload.permission.value,
        },
    )
    await db.commit()
    await db.refresh(grant)
    return _to_read(grant, name)


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grant(
    grant_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke an explicit grant. Requires ``manager`` on the grant's resource."""
    grant = (
        await db.execute(
            select(AccessGrant).where(AccessGrant.id == grant_id, AccessGrant.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Access grant not found")

    await require_permission(
        db, ctx, grant.resource_type, grant.resource_id, PermissionLevel.MANAGER
    )

    resource_type = grant.resource_type
    resource_id = grant.resource_id
    meta = {
        "principal_type": grant.principal_type.value,
        "principal_id": str(grant.principal_id),
        "permission": grant.permission.value,
    }
    await db.delete(grant)
    ip, ua = _client_meta(request)
    await record_audit(
        db,
        ctx,
        AuditAction.PERMISSION_REVOKED.value,
        resource_type=resource_type.value,
        resource_id=resource_id,
        ip_address=ip,
        user_agent=ua,
        meta=meta,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _validate_principal(
    db: AsyncSession,
    ctx: AuthContext,
    principal_type: PrincipalType,
    principal_id: uuid.UUID,
) -> str:
    """Ensure the principal exists in the org; return its display name."""
    if principal_type == PrincipalType.USER:
        row = (
            await db.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    User.id == principal_id,
                    Membership.org_id == ctx.org_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=400,
                detail="Principal user is not a member of this organization",
            )
        return row.full_name or row.email

    team = (
        await db.execute(select(Team).where(Team.id == principal_id, Team.org_id == ctx.org_id))
    ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=400, detail="Principal team not found in this organization")
    return team.name
