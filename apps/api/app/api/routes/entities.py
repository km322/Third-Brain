"""Entity index: browse extracted entities and the documents that mention them.

Both endpoints are permission-scoped: an entity is listed only when the caller can see at
least one document mentioning it, and ``document_count`` / the document list reflect only
caller-visible documents. Scoping reuses ``build_retrieval_scope`` so it matches search.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, require_read_scope
from app.models.document import Document
from app.models.entity import DocumentEntity, Entity
from app.models.enums import EntityKind
from app.schemas.document import DocumentItem
from app.schemas.entity import EntityRead
from app.services.permissions import RetrievalScope, build_retrieval_scope

router = APIRouter(prefix="/entities", tags=["entities"])


def _visible_doc_filters(scope: RetrievalScope) -> list[ColumnElement[bool]] | None:
    """Predicate list restricting ``Document`` to the caller's scope, or None for no access."""
    filters: list[ColumnElement[bool]] = [Document.org_id == scope.org_id]
    if scope.all_access:
        return filters
    allow = []
    if scope.collection_ids:
        allow.append(Document.collection_id.in_(scope.collection_ids))
    if scope.extra_document_ids:
        allow.append(Document.id.in_(scope.extra_document_ids))
    if not allow:
        return None
    filters.append(or_(*allow))
    if scope.denied_document_ids:
        filters.append(Document.id.notin_(scope.denied_document_ids))
    return filters


@router.get("", response_model=list[EntityRead])
async def list_entities(
    kind: EntityKind | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> list[EntityRead]:
    """List entities that appear in caller-visible documents, most-mentioned first."""
    scope = await build_retrieval_scope(db, ctx)
    doc_filters = _visible_doc_filters(scope)
    if doc_filters is None:
        return []
    conditions = [Entity.org_id == ctx.org_id, *doc_filters]
    if kind is not None:
        conditions.append(Entity.kind == kind)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(Entity.name.ilike(f"%{escaped}%", escape="\\"))

    doc_count = func.count(func.distinct(DocumentEntity.document_id))
    rows = (
        await db.execute(
            select(Entity, doc_count)
            .join(DocumentEntity, DocumentEntity.entity_id == Entity.id)
            .join(Document, Document.id == DocumentEntity.document_id)
            .where(*conditions)
            .group_by(Entity.id)
            .order_by(doc_count.desc(), Entity.name.asc())
            .limit(limit)
        )
    ).all()
    return [
        EntityRead(id=entity.id, kind=entity.kind, name=entity.name, document_count=count)
        for entity, count in rows
    ]


@router.get("/{entity_id}/documents", response_model=list[DocumentItem])
async def entity_documents(
    entity_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> list[DocumentItem]:
    """List caller-visible documents that mention the entity, newest first."""
    entity = await db.get(Entity, entity_id)
    if entity is None or entity.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    scope = await build_retrieval_scope(db, ctx)
    doc_filters = _visible_doc_filters(scope)
    if doc_filters is None:
        return []
    rows = (
        (
            await db.execute(
                select(Document)
                .join(DocumentEntity, DocumentEntity.document_id == Document.id)
                .where(DocumentEntity.entity_id == entity_id, *doc_filters)
                .order_by(Document.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [DocumentItem.model_validate(d) for d in rows]
