"""Knowledge-graph endpoints.

* ``GET /graph`` - the caller's whole visible document-similarity graph (documents as
  nodes, content similarity as edges).
* ``GET /graph/documents/{document_id}/neighbors`` - one document's nearest neighbors.

Authorization is delegated entirely to the retrieval scope: the permission engine
restricts both surfaces to documents the caller may view, so a node (or an edge endpoint)
the caller cannot see can never appear. Like ``/search``, API keys need the ``search``
scope while dashboard sessions pass; both are per-API-key rate limited. These routes are
read-only, so they never commit.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, enforce_rate_limit, require_scope
from app.schemas.graph import GraphNeighbors, GraphResponse
from app.services import graph

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("", response_model=GraphResponse)
async def get_graph(
    collection_id: uuid.UUID | None = Query(
        default=None, description="Restrict the graph to a single collection."
    ),
    limit: int = Query(default=400, ge=1, le=1000, description="Max nodes (richest win)."),
    min_similarity: float = Query(
        default=0.15, ge=0.0, le=1.0, description="Minimum cosine similarity for an edge."
    ),
    max_neighbors: int = Query(
        default=8, ge=1, le=20, description="Max strongest edges kept per node."
    ),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("search")),
) -> GraphResponse:
    """Return the caller's permission-scoped document-similarity graph."""
    await enforce_rate_limit(ctx)
    return await graph.build_document_graph(
        db,
        ctx,
        collection_id=collection_id,
        limit=limit,
        min_similarity=min_similarity,
        max_neighbors=max_neighbors,
    )


@router.get("/documents/{document_id}/neighbors", response_model=GraphNeighbors)
async def get_neighbors(
    document_id: uuid.UUID,
    limit: int = Query(default=12, ge=1, le=50, description="Max neighbors to return."),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("search")),
) -> GraphNeighbors:
    """Return a document's nearest visible neighbors (404 if it is not visible)."""
    await enforce_rate_limit(ctx)
    return await graph.neighbors(db, ctx, document_id=document_id, limit=limit)
