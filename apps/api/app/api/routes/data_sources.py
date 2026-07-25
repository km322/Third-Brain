"""Data-source (knowledge connector) management + identity mapping.

A data source syncs documents and their source-system ACLs into a target collection.
Reads are available to any org member; all mutations (create/update/delete/sync) and
identity mapping require an org admin. The synced ACL is materialised as ordinary
``AccessGrant`` rows, so retrieval enforcement is unchanged.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context, require_role
from app.core.security import encrypt_secret
from app.models.collection import Collection
from app.models.datasource import DataSource, DocumentExternalPrincipal, ExternalIdentity
from app.models.enums import AuditAction, ExternalPrincipalKind, MembershipStatus, OrgRole
from app.models.team import Team
from app.models.user import Membership
from app.schemas.common import Message
from app.schemas.data_source import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    ExternalPrincipalItem,
    IdentityMapCreate,
    IdentityMapResult,
    IdentityRead,
    SyncResult,
)
from app.services.connectors import get_connector  # noqa: F401  (ensures registry import)
from app.services.connectors.base import ConnectorError
from app.services.connectors.registry import connector_class
from app.services.datasource_sync import map_identity, sync_data_source
from app.services.metering import record_audit

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


async def _get_owned(db: AsyncSession, ctx: AuthContext, source_id: uuid.UUID) -> DataSource:
    source = await db.get(DataSource, source_id)
    if source is None or source.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    return source


async def _require_org_collection(
    db: AsyncSession, ctx: AuthContext, collection_id: uuid.UUID
) -> Collection:
    collection = await db.get(Collection, collection_id)
    if collection is None or collection.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return collection


def _validate_config(kind, config: dict, secret: str | None) -> None:
    try:
        connector_class(kind).validate_config(config or {}, secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[DataSourceRead])
async def list_data_sources(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[DataSourceRead]:
    rows = (
        (
            await db.execute(
                select(DataSource)
                .where(DataSource.org_id == ctx.org_id)
                .order_by(DataSource.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [DataSourceRead.from_model(s) for s in rows]


@router.post("", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
async def create_data_source(
    payload: DataSourceCreate,
    request: Request,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> DataSourceRead:
    await _require_org_collection(db, ctx, payload.collection_id)
    _validate_config(payload.kind, payload.config, payload.secret)

    source = DataSource(
        org_id=ctx.org_id,
        collection_id=payload.collection_id,
        created_by_id=ctx.user_id,
        name=payload.name,
        kind=payload.kind,
        config=payload.config or {},
        encrypted_secret=encrypt_secret(payload.secret) if payload.secret else None,
        default_visibility=payload.default_visibility,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    db.add(source)
    await db.flush()
    await record_audit(
        db,
        ctx,
        AuditAction.DATA_SOURCE_CREATED.value,
        resource_type="data_source",
        resource_id=source.id,
        ip_address=request.client.host if request.client else None,
        meta={"kind": payload.kind.value, "name": payload.name},
    )
    await db.commit()
    await db.refresh(source)
    return DataSourceRead.from_model(source)


@router.get("/{source_id}", response_model=DataSourceRead)
async def get_data_source(
    source_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> DataSourceRead:
    return DataSourceRead.from_model(await _get_owned(db, ctx, source_id))


@router.patch("/{source_id}", response_model=DataSourceRead)
async def update_data_source(
    source_id: uuid.UUID,
    payload: DataSourceUpdate,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> DataSourceRead:
    source = await _get_owned(db, ctx, source_id)
    fields_set = payload.model_fields_set

    if payload.name is not None:
        source.name = payload.name
    if payload.config is not None:
        source.config = payload.config
    if payload.default_visibility is not None:
        source.default_visibility = payload.default_visibility
    if "sync_interval_minutes" in fields_set:
        source.sync_interval_minutes = payload.sync_interval_minutes
    if payload.status is not None:
        source.status = payload.status
    if "secret" in fields_set:
        source.encrypted_secret = encrypt_secret(payload.secret) if payload.secret else None

    # Re-validate the resulting config/secret so an update can't leave it unusable.
    _validate_config(
        source.kind,
        source.config,
        payload.secret if "secret" in fields_set else (source.encrypted_secret and "***"),
    )
    await record_audit(
        db,
        ctx,
        AuditAction.DATA_SOURCE_UPDATED.value,
        resource_type="data_source",
        resource_id=source.id,
        meta={"fields": sorted(fields_set)},
    )
    await db.commit()
    await db.refresh(source)
    return DataSourceRead.from_model(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_source(
    source_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    source = await _get_owned(db, ctx, source_id)
    await record_audit(
        db,
        ctx,
        AuditAction.DATA_SOURCE_DELETED.value,
        resource_type="data_source",
        resource_id=source.id,
        meta={"name": source.name},
    )
    # Documents (and their chunks/principals) plus source-scoped grants cascade via FKs.
    await db.delete(source)
    await db.commit()


@router.post("/{source_id}/sync", response_model=SyncResult)
async def trigger_sync(
    source_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SyncResult:
    """Run a sync now (inline). Pulls documents + source ACLs into the target collection."""
    if not settings.DATA_SOURCE_SYNC_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Data-source sync is disabled"
        )
    source = await _get_owned(db, ctx, source_id)
    try:
        stats = await sync_data_source(db, source.id)
    except ConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SyncResult(**stats.as_dict())


@router.get("/{source_id}/principals", response_model=list[ExternalPrincipalItem])
async def list_source_principals(
    source_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[ExternalPrincipalItem]:
    """Distinct source principals seen across this source's documents, with mapping status."""
    source = await _get_owned(db, ctx, source_id)
    rows = (
        await db.execute(
            select(
                DocumentExternalPrincipal.provider,
                DocumentExternalPrincipal.external_id,
                DocumentExternalPrincipal.kind,
                func.count(func.distinct(DocumentExternalPrincipal.document_id)),
            )
            .where(DocumentExternalPrincipal.source_id == source.id)
            .group_by(
                DocumentExternalPrincipal.provider,
                DocumentExternalPrincipal.external_id,
                DocumentExternalPrincipal.kind,
            )
        )
    ).all()

    identities = (
        (
            await db.execute(
                select(ExternalIdentity).where(
                    ExternalIdentity.org_id == ctx.org_id,
                    ExternalIdentity.provider == source.provider,
                )
            )
        )
        .scalars()
        .all()
    )
    by_ext = {i.external_id: i for i in identities}

    items: list[ExternalPrincipalItem] = []
    for provider, external_id, kind, doc_count in rows:
        ident = by_ext.get(external_id)
        items.append(
            ExternalPrincipalItem(
                provider=provider,
                external_id=external_id,
                kind=kind,
                document_count=doc_count,
                mapped=ident is not None and (ident.user_id or ident.team_id) is not None,
                mapped_user_id=ident.user_id if ident else None,
                mapped_team_id=ident.team_id if ident else None,
            )
        )
    return items


@router.get("/identities/all", response_model=list[IdentityRead])
async def list_identities(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[IdentityRead]:
    rows = (
        (
            await db.execute(
                select(ExternalIdentity)
                .where(ExternalIdentity.org_id == ctx.org_id)
                .order_by(ExternalIdentity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [IdentityRead.model_validate(i) for i in rows]


@router.post("/identities", response_model=IdentityMapResult, status_code=status.HTTP_201_CREATED)
async def create_identity_mapping(
    payload: IdentityMapCreate,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> IdentityMapResult:
    """Map a source principal to a Third Brain user/team and backfill its document grants."""
    if bool(payload.user_id) == bool(payload.team_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of user_id / team_id",
        )
    if payload.user_id is not None:
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.org_id == ctx.org_id,
                    Membership.user_id == payload.user_id,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User is not an active member of this organization",
            )
    if payload.team_id is not None:
        team = await db.get(Team, payload.team_id)
        if team is None or team.org_id != ctx.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    backfilled = await map_identity(
        db,
        ctx.org_id,
        payload.provider,
        payload.external_id,
        payload.kind if isinstance(payload.kind, ExternalPrincipalKind) else payload.kind,
        user_id=payload.user_id,
        team_id=payload.team_id,
    )
    await record_audit(
        db,
        ctx,
        AuditAction.IDENTITY_MAPPED.value,
        resource_type="external_identity",
        meta={
            "provider": payload.provider,
            "external_id": payload.external_id,
            "grants_backfilled": backfilled,
        },
    )
    await db.commit()

    ident = (
        await db.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.org_id == ctx.org_id,
                ExternalIdentity.provider == payload.provider,
                ExternalIdentity.external_id == payload.external_id,
            )
        )
    ).scalar_one()
    return IdentityMapResult(
        identity=IdentityRead.model_validate(ident), grants_backfilled=backfilled
    )


@router.delete("/identities/{identity_id}", response_model=Message)
async def delete_identity_mapping(
    identity_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> Message:
    """Remove an identity mapping. Grants previously materialised from it remain until
    the next sync recomputes ACLs (a re-sync will drop grants for the now-unmapped principal)."""
    ident = await db.get(ExternalIdentity, identity_id)
    if ident is None or ident.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Identity not found")
    await db.delete(ident)
    await db.commit()
    return Message(detail="Identity mapping removed")
