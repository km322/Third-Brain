"""The permission-scoped document-similarity graph.

Nodes are indexed documents the caller may view; an edge joins two documents whose
content is similar (cosine similarity of their per-document chunk centroids). Visibility
is resolved through the SAME permission engine as search
(:func:`app.services.permissions.build_retrieval_scope`), so a document the caller cannot
see never becomes a node and never an edge endpoint -- the product's core invariant.

Per-document centroids are computed with pgvector's ``avg(vector)`` aggregate. Edges come
from a per-node top-``max_neighbors`` ``1 - cosine_distance`` LATERAL join over the capped
centroid set, so at most ``limit * max_neighbors`` edge rows reach Python instead of the
full pairwise product. The whole result is cached in Redis keyed by the caller's EXACT
visible-document set (a hash of the sorted ids) in addition to the org and query
parameters, so the cache can never serve one principal documents another principal cannot
see. The single-document ``neighbors`` view shares the same visibility-keyed cache.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict

from fastapi import HTTPException, status
from pgvector.sqlalchemy import avg as vector_avg
from sqlalchemy import func, select, true
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.core.redis import cache_get_json, cache_set_json
from app.core.telemetry import get_tracer
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import DocumentStatus, SourceType
from app.schemas.graph import GraphEdge, GraphNeighbors, GraphNode, GraphResponse
from app.services.permissions import RetrievalScope, build_retrieval_scope

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Similarity graphs are stable over short windows and expensive to compute, so cache the
# whole payload briefly. The key varies per caller-visibility (see ``_cache_key``).
_GRAPH_CACHE_TTL_SECONDS = 300

# Upper bound on how many visible documents the single-document ``neighbors`` view scans
# for centroids: the richest documents win, mirroring the whole-graph node cap.
_NEIGHBOR_CANDIDATE_CAP = 1000

_NODE_COLUMNS = (
    Document.id,
    Document.title,
    Document.collection_id,
    Collection.name.label("collection_name"),
    Document.chunk_count,
    Document.source_type,
    Document.created_at,
)


def _node_from_row(row: Row, degree: int) -> GraphNode:
    source_type = row.source_type
    return GraphNode(
        id=row.id,
        title=row.title,
        collection_id=row.collection_id,
        collection_name=row.collection_name,
        chunk_count=row.chunk_count,
        source_type=source_type.value if isinstance(source_type, SourceType) else str(source_type),
        created_at=row.created_at,
        degree=degree,
    )


def _canonical_edge(a: uuid.UUID, b: uuid.UUID, weight: float) -> GraphEdge:
    """Emit an undirected edge once, with the lexicographically smaller id as source."""
    sa, sb = str(a), str(b)
    source, target = (sa, sb) if sa <= sb else (sb, sa)
    return GraphEdge(source=source, target=target, weight=round(weight, 6))


def _visible_indexed_chunk_select(scope: RetrievalScope):
    """A permission-scoped select of the document ids the caller may view that are
    indexed and carry at least one embedded chunk."""
    stmt = (
        select(DocumentChunk.document_id)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.embedding.is_not(None),
            Document.status == DocumentStatus.INDEXED,
        )
    )
    return scope.apply(stmt)


def _cache_key(
    ctx: AuthContext,
    collection_id: uuid.UUID | None,
    limit: int,
    min_similarity: float,
    max_neighbors: int,
    total_visible: int,
    ids: list[uuid.UUID],
    *,
    center_id: uuid.UUID | None = None,
) -> str:
    # Hash the exact capped visible-id set so two principals only ever share a cached graph
    # when they see exactly the same documents. ``total_visible`` disambiguates callers whose
    # capped set is identical but whose full visible count (and thus ``truncated``) differs.
    # ``center_id`` namespaces the single-document neighbours subgraph so it can never
    # collide with the whole-org graph key.
    digest = hashlib.sha256(",".join(sorted(str(i) for i in ids)).encode("utf-8")).hexdigest()
    collection = str(collection_id) if collection_id else "all"
    prefix = f"graph-neighbors:{center_id}" if center_id is not None else "graph"
    return (
        f"{prefix}:{ctx.org_id}:{collection}:{limit}:{min_similarity:.4f}:"
        f"{max_neighbors}:{total_visible}:{digest[:32]}"
    )


async def _fetch_nodes(
    db: AsyncSession, scope: RetrievalScope, limit: int
) -> tuple[list[Row], int]:
    """Return the capped node rows (richest documents first) and the total visible count."""
    visible = _visible_indexed_chunk_select(scope).distinct().subquery()
    total_visible = int(await db.scalar(select(func.count()).select_from(visible)) or 0)
    rows = (
        await db.execute(
            select(*_NODE_COLUMNS)
            .join(Collection, Collection.id == Document.collection_id)
            .where(Document.id.in_(select(visible.c.document_id)))
            # Richest documents win the cap: most chunks, then most recently indexed/created.
            .order_by(
                Document.chunk_count.desc(),
                Document.indexed_at.desc().nulls_last(),
                Document.created_at.desc(),
                Document.id,
            )
            .limit(limit)
        )
    ).all()
    return list(rows), total_visible


async def _pruned_edges(
    db: AsyncSession,
    node_ids: list[uuid.UUID],
    min_similarity: float,
    max_neighbors: int,
) -> dict[tuple[str, str], float]:
    """Each node's strongest ``max_neighbors`` cosine-similarity edges over the capped
    centroid set.

    A per-node ``ORDER BY cosine_distance LIMIT max_neighbors`` LATERAL join (mirroring
    :func:`neighbors`) keeps only each node's nearest neighbours in SQL, so at most
    ``len(node_ids) * max_neighbors`` rows reach Python instead of the full ``C(n, 2)``
    pairwise product. Because weight is monotonic in distance, applying ``min_similarity``
    as the outer filter yields the same edge set as filtering before the per-node top-k.
    The resulting graph is undirected: a canonical (smaller id first) pair kept by EITHER
    endpoint survives. Returns ``{(source, target): weight}``.
    """
    if len(node_ids) < 2:
        return {}

    centroids = (
        select(
            DocumentChunk.document_id.label("document_id"),
            vector_avg(DocumentChunk.embedding).label("centroid"),
        )
        .where(DocumentChunk.document_id.in_(node_ids), DocumentChunk.embedding.is_not(None))
        .group_by(DocumentChunk.document_id)
        .cte("doc_centroids")
    )
    left = centroids.alias("c1")
    right = centroids.alias("c2")
    distance = left.c.centroid.cosine_distance(right.c.centroid)
    top_neighbors = (
        select(right.c.document_id.label("document_id"), right.c.centroid.label("centroid"))
        .where(right.c.document_id != left.c.document_id)
        .order_by(distance.asc(), right.c.document_id.asc())
        .limit(max_neighbors)
        .lateral("top_neighbors")
    )
    weight = 1 - left.c.centroid.cosine_distance(top_neighbors.c.centroid)
    stmt = (
        select(
            left.c.document_id.label("source"),
            top_neighbors.c.document_id.label("target"),
            weight.label("weight"),
        )
        .select_from(left.join(top_neighbors, true()))
        .where(weight >= min_similarity)
    )
    rows = (await db.execute(stmt)).all()

    weight_by_pair: dict[tuple[str, str], float] = {}
    for row in rows:
        source, target = str(row.source), str(row.target)
        pair = (source, target) if source <= target else (target, source)
        weight_by_pair[pair] = float(row.weight)
    return weight_by_pair


async def build_document_graph(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    collection_id: uuid.UUID | None = None,
    limit: int = 400,
    min_similarity: float = 0.15,
    max_neighbors: int = 8,
) -> GraphResponse:
    """Build the caller's permission-scoped document-similarity graph."""
    with tracer.start_as_current_span("graph.build") as span:
        span.set_attribute("app.limit", limit)
        span.set_attribute("app.min_similarity", min_similarity)
        span.set_attribute("app.max_neighbors", max_neighbors)

        scope = await build_retrieval_scope(
            db, ctx, collection_ids=[collection_id] if collection_id else None
        )
        node_rows, total_visible = await _fetch_nodes(db, scope, limit)
        capped_ids = [row.id for row in node_rows]
        truncated = total_visible > limit

        cache_key = _cache_key(
            ctx, collection_id, limit, min_similarity, max_neighbors, total_visible, capped_ids
        )
        cached = None
        try:
            cached = await cache_get_json(cache_key)
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("Graph cache read failed: %s", exc)

        if cached is not None:
            response = GraphResponse(**cached)
        else:
            kept = await _pruned_edges(db, capped_ids, min_similarity, max_neighbors)
            degree: dict[str, int] = defaultdict(int)
            for source, target in kept:
                degree[source] += 1
                degree[target] += 1
            nodes = [_node_from_row(row, degree.get(str(row.id), 0)) for row in node_rows]
            edges = [
                _canonical_edge(uuid.UUID(source), uuid.UUID(target), weight)
                for (source, target), weight in sorted(kept.items())
            ]
            response = GraphResponse(
                nodes=nodes,
                edges=edges,
                truncated=truncated,
                total_visible=total_visible,
                limit=limit,
                min_similarity=min_similarity,
            )
            try:
                await cache_set_json(
                    cache_key, response.model_dump(mode="json"), _GRAPH_CACHE_TTL_SECONDS
                )
            except Exception as exc:  # pragma: no cover - cache is best-effort
                logger.warning("Graph cache write failed: %s", exc)

        span.set_attribute("app.node_count", len(response.nodes))
        span.set_attribute("app.edge_count", len(response.edges))
        logger.info(
            "graph_built",
            org_id=str(ctx.org_id),
            nodes=len(response.nodes),
            edges=len(response.edges),
            truncated=response.truncated,
        )
        return response


async def _fetch_center(
    db: AsyncSession, scope: RetrievalScope, document_id: uuid.UUID
) -> Row | None:
    """Return the center document's node row, or ``None`` when it is not visible."""
    visible = (
        _visible_indexed_chunk_select(scope)
        .where(DocumentChunk.document_id == document_id)
        .limit(1)
        .subquery()
    )
    return (
        await db.execute(
            select(*_NODE_COLUMNS)
            .join(Collection, Collection.id == Document.collection_id)
            .where(Document.id.in_(select(visible.c.document_id)))
        )
    ).first()


async def _neighbor_candidate_ids(
    db: AsyncSession, scope: RetrievalScope, document_id: uuid.UUID, cap: int
) -> list[uuid.UUID]:
    """The visible indexed documents (excluding the center) whose centroids the neighbours
    view considers, capped to the ``cap`` richest so an org with a huge visible corpus
    never aggregates a centroid for every document. Mirrors :func:`_fetch_nodes`' ordering.
    Doubles as the exact id set hashed into the cache key, so the cache stays
    visibility-safe."""
    visible = (
        _visible_indexed_chunk_select(scope)
        .where(DocumentChunk.document_id != document_id)
        .distinct()
        .subquery()
    )
    rows = (
        await db.execute(
            select(Document.id)
            .where(Document.id.in_(select(visible.c.document_id)))
            .order_by(
                Document.chunk_count.desc(),
                Document.indexed_at.desc().nulls_last(),
                Document.created_at.desc(),
                Document.id,
            )
            .limit(cap)
        )
    ).all()
    return [row.id for row in rows]


async def _compute_neighbors(
    db: AsyncSession,
    center: Row,
    document_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
    limit: int,
) -> GraphNeighbors:
    """The center document plus its top-``limit`` most-similar candidates by cosine
    similarity of per-document chunk centroids."""
    neighbor_ids: list[uuid.UUID] = []
    weight_by_id: dict[uuid.UUID, float] = {}
    if candidate_ids:
        center_centroid = (
            select(vector_avg(DocumentChunk.embedding))
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.embedding.is_not(None),
            )
            .scalar_subquery()
        )
        candidate_centroids = (
            select(
                DocumentChunk.document_id.label("document_id"),
                vector_avg(DocumentChunk.embedding).label("centroid"),
            )
            .where(
                DocumentChunk.document_id.in_(candidate_ids),
                DocumentChunk.embedding.is_not(None),
            )
            .group_by(DocumentChunk.document_id)
            .cte("cand_centroids")
        )
        distance = candidate_centroids.c.centroid.cosine_distance(center_centroid)
        neighbor_rows = (
            await db.execute(
                select(
                    candidate_centroids.c.document_id.label("document_id"),
                    (1 - distance).label("weight"),
                )
                .order_by(distance.asc())
                .limit(limit)
            )
        ).all()
        neighbor_ids = [row.document_id for row in neighbor_rows]
        weight_by_id = {row.document_id: float(row.weight) for row in neighbor_rows}

    meta_by_id: dict[uuid.UUID, Row] = {}
    if neighbor_ids:
        meta_rows = (
            await db.execute(
                select(*_NODE_COLUMNS)
                .join(Collection, Collection.id == Document.collection_id)
                .where(Document.id.in_(neighbor_ids))
            )
        ).all()
        meta_by_id = {row.id: row for row in meta_rows}

    ordered = [meta_by_id[i] for i in neighbor_ids if i in meta_by_id]
    nodes = [_node_from_row(center, len(ordered))]
    nodes.extend(_node_from_row(row, 1) for row in ordered)
    edges = [_canonical_edge(document_id, row.id, weight_by_id[row.id]) for row in ordered]
    return GraphNeighbors(center_id=document_id, nodes=nodes, edges=edges)


async def neighbors(
    db: AsyncSession,
    ctx: AuthContext,
    *,
    document_id: uuid.UUID,
    limit: int = 12,
) -> GraphNeighbors:
    """Return the center document plus its top-``limit`` most-similar visible documents.

    Raises 404 when the document is not visible to the caller, so the endpoint never leaks
    the existence of an out-of-scope document. The result is cached in Redis under the same
    visibility-keyed scheme as :func:`build_document_graph` (keyed on org + the sorted
    visible candidate ids + the center document + limit), so the center's visibility is
    always re-checked before a cached payload is served and no cache entry can be shared
    across principals who see different documents.
    """
    with tracer.start_as_current_span("graph.neighbors") as span:
        span.set_attribute("app.limit", limit)

        scope = await build_retrieval_scope(db, ctx)
        center = await _fetch_center(db, scope, document_id)
        if center is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        candidate_ids = await _neighbor_candidate_ids(
            db, scope, document_id, _NEIGHBOR_CANDIDATE_CAP
        )
        cache_key = _cache_key(ctx, None, limit, 0.0, 0, 0, candidate_ids, center_id=document_id)
        cached = None
        try:
            cached = await cache_get_json(cache_key)
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("Graph neighbors cache read failed: %s", exc)

        if cached is not None:
            response = GraphNeighbors(**cached)
        else:
            response = await _compute_neighbors(db, center, document_id, candidate_ids, limit)
            try:
                await cache_set_json(
                    cache_key, response.model_dump(mode="json"), _GRAPH_CACHE_TTL_SECONDS
                )
            except Exception as exc:  # pragma: no cover - cache is best-effort
                logger.warning("Graph neighbors cache write failed: %s", exc)

        span.set_attribute("app.node_count", len(response.nodes))
        logger.info(
            "graph_built",
            org_id=str(ctx.org_id),
            nodes=len(response.nodes),
            edges=len(response.edges),
            truncated=False,
        )
        return response
