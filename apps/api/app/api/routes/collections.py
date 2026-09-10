"""Collections (knowledge bases) API.

CRUD over collections with permission enforcement:

* **List** is permission-filtered - only collections the caller can view (via the
  retrieval scope: ownership, visibility or explicit grants) are returned.
* **Read** requires ``VIEWER``.
* **Create** requires an org role of ``editor`` or higher.
* **Update / delete** require ``MANAGER`` on the collection (owners get this for free).

Every query is scoped to ``ctx.org_id`` so organizations never see each other's data.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context, role_at_least
from app.models.access import AccessGrant
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import OrgRole, PermissionLevel, ResourceType
from app.schemas.collection import CollectionCreate, CollectionRead, CollectionUpdate
from app.schemas.common import Message
from app.services.metering import record_audit
from app.services.permissions import build_retrieval_scope, require_permission

router = APIRouter(prefix="/collections", tags=["collections"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "collection"


async def _unique_slug(db: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    """Return a slug unique within the org (append -2, -3, … on collision)."""
    base = _slugify(name)
    existing = set(
        (
            await db.execute(
                select(Collection.slug).where(
                    Collection.org_id == org_id, Collection.slug.like(f"{base}%")
                )
            )
        ).scalars()
    )
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


async def _get_owned_collection(
    db: AsyncSession, ctx: AuthContext, collection_id: uuid.UUID
) -> Collection:
    """Fetch an org-scoped collection or raise 404."""
    collection = await db.get(Collection, collection_id)
    if collection is None or collection.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


def _to_read(collection: Collection, permission: PermissionLevel | None = None) -> CollectionRead:
    read = CollectionRead.model_validate(collection)
    read.permission = permission
    return read


@router.get("", response_model=list[CollectionRead])
async def list_collections(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> list[CollectionRead]:
    """List collections the caller can view, most recent first."""
    scope = await build_retrieval_scope(db, ctx)
    stmt = select(Collection).where(Collection.org_id == ctx.org_id)
    if not scope.all_access:
        if not scope.collection_ids:
            return []
        stmt = stmt.where(Collection.id.in_(scope.collection_ids))
    stmt = stmt.order_by(Collection.created_at.desc())
    collections = (await db.execute(stmt)).scalars().all()
    return [_to_read(c) for c in collections]


@router.post("", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
async def create_collection(
    payload: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CollectionRead:
    """Create a new knowledge base. Requires an org role of editor or higher.

    A per-collection ``embedding_model`` override is rejected: all chunks share one global
    ``vector(EMBEDDING_DIM)`` column and queries embed with the org's single embedding
    provider, so a model in a different embedding space cannot be honored - it would either
    fail to insert (wrong dimension) or silently return garbage (same dimension, different
    space). Reject it rather than accept a setting we cannot keep correct.
    """
    if not role_at_least(ctx.org_role, OrgRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires org role 'editor' or higher to create a collection",
        )
    if payload.embedding_model and payload.embedding_model != settings.EMBEDDING_MODEL:
        raise HTTPException(
            status_code=422,
            detail=(
                "Per-collection embedding_model overrides are not supported; "
                f"the platform embedding model is '{settings.EMBEDDING_MODEL}'."
            ),
        )
    slug = await _unique_slug(db, ctx.org_id, payload.name)
    collection = Collection(
        org_id=ctx.org_id,
        owner_id=ctx.user_id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        visibility=payload.visibility,
        default_permission=payload.default_permission,
        embedding_model=payload.embedding_model or settings.EMBEDDING_MODEL,
        embedding_dim=settings.EMBEDDING_DIM,
        document_count=0,
    )
    db.add(collection)
    await db.flush()

    await record_audit(
        db,
        ctx,
        "collection.created",
        resource_type=ResourceType.COLLECTION.value,
        resource_id=collection.id,
        meta={"name": collection.name},
    )
    await db.commit()
    await db.refresh(collection)
    return _to_read(collection, PermissionLevel.MANAGER)


@router.get("/{collection_id}", response_model=CollectionRead)
async def get_collection(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CollectionRead:
    """Fetch a single collection. Requires VIEWER permission."""
    collection = await _get_owned_collection(db, ctx, collection_id)
    perm = await require_permission(
        db, ctx, ResourceType.COLLECTION, collection_id, PermissionLevel.VIEWER
    )
    return _to_read(collection, perm)


@router.patch("/{collection_id}", response_model=CollectionRead)
async def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> CollectionRead:
    """Update collection metadata/visibility. Requires MANAGER permission."""
    collection = await _get_owned_collection(db, ctx, collection_id)
    await require_permission(
        db, ctx, ResourceType.COLLECTION, collection_id, PermissionLevel.MANAGER
    )

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        collection.name = data["name"]
    if "description" in data:
        collection.description = data["description"]
    if "visibility" in data and data["visibility"] is not None:
        collection.visibility = data["visibility"]
    if "default_permission" in data and data["default_permission"] is not None:
        collection.default_permission = data["default_permission"]

    await db.commit()
    await db.refresh(collection)
    return _to_read(collection, PermissionLevel.MANAGER)


@router.delete("/{collection_id}", response_model=Message)
async def delete_collection(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> Message:
    """Delete a collection and all of its documents/chunks. Requires MANAGER.

    Stored blobs are cleaned up on a best-effort basis before the cascade removes the rows.

    Grants on the collection or any of its (cascade-deleted) documents have no FK to the
    resource, so they are purged here to avoid stranded ACL rows that could later re-apply.
    An empty ``doc_ids`` makes the document branch match nothing.
    """
    collection = await _get_owned_collection(db, ctx, collection_id)
    await require_permission(
        db, ctx, ResourceType.COLLECTION, collection_id, PermissionLevel.MANAGER
    )

    from app.services.storage import get_storage

    storage = get_storage()
    storage_keys = (
        (
            await db.execute(
                select(Document.storage_key).where(
                    Document.collection_id == collection.id,
                    Document.storage_key.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for key in storage_keys:
        try:
            await storage.delete(key)
        except Exception:  # pragma: no cover - storage cleanup is best-effort
            pass

    doc_ids = (
        (await db.execute(select(Document.id).where(Document.collection_id == collection.id)))
        .scalars()
        .all()
    )
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.org_id == ctx.org_id,
            or_(
                and_(
                    AccessGrant.resource_type == ResourceType.COLLECTION,
                    AccessGrant.resource_id == collection.id,
                ),
                and_(
                    AccessGrant.resource_type == ResourceType.DOCUMENT,
                    AccessGrant.resource_id.in_(doc_ids),
                ),
            ),
        )
    )

    await record_audit(
        db,
        ctx,
        "collection.deleted",
        resource_type=ResourceType.COLLECTION.value,
        resource_id=collection.id,
        meta={"name": collection.name},
    )
    await db.delete(collection)
    await db.commit()
    return Message(detail="Collection deleted")
