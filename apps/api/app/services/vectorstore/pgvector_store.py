"""pgvector-backed vector store with permission-aware filtering."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.services.permissions import RetrievalScope
from app.services.vectorstore.base import SearchHit

# The HNSW index returns its nearest candidates BEFORE the permission/org predicate is
# applied as a post-filter. With the default ``ef_search`` (40) and a single index shared
# by every org, a small tenant's rows can be crowded out of the candidate set by a large
# tenant's, so a filtered search silently returns far fewer than ``top_k`` (often zero).
# We widen ``ef_search`` per query and enable pgvector 0.8's iterative scan so the index
# keeps fetching candidates until enough survive the filter.
_MIN_EF_SEARCH = 100


class PgVectorStore:
    """Cosine ANN search over ``document_chunks.embedding``.

    The permission ``scope`` predicate is applied INSIDE the SQL query, so filtered-out
    chunks never leave the database.
    """

    async def similarity_search(
        self,
        db: AsyncSession,
        scope: RetrievalScope,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchHit]:
        if scope.is_empty:
            return []
        # set_config(..., is_local=true) is scoped to the surrounding transaction, so it
        # only affects this search and never leaks into unrelated statements on the pooled
        # connection. (SET LOCAL cannot take a bind parameter; set_config can.) Both settings
        # go out in a single round-trip.
        ef_search = max(_MIN_EF_SEARCH, top_k * 2)
        await db.execute(
            text(
                "SELECT set_config('hnsw.ef_search', :ef, true), "
                "set_config('hnsw.iterative_scan', 'relaxed_order', true)"
            ).bindparams(ef=str(ef_search))
        )
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.collection_id,
                DocumentChunk.content,
                DocumentChunk.chunk_index,
                DocumentChunk.meta,
                distance.label("distance"),
                Document.title,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            # Exclude only QUARANTINED documents: chunks exist solely from a prior
            # successful index, so this hides leftover chunks of a now-quarantined
            # document while still surfacing content that is mid-reprocess (PENDING/
            # PROCESSING) or left indexed after a failed reprocess - matching the
            # pre-quarantine behaviour, which had no status predicate at all.
            .where(
                DocumentChunk.embedding.is_not(None),
                Document.status != DocumentStatus.QUARANTINED,
            )
            .order_by(distance.asc())
            .limit(top_k)
        )
        stmt = scope.apply(stmt)
        rows = (await db.execute(stmt)).all()
        return [
            SearchHit(
                chunk_id=r.id,
                document_id=r.document_id,
                collection_id=r.collection_id,
                content=r.content,
                chunk_index=r.chunk_index,
                metadata=r.meta or {},
                score=1.0 - float(r.distance),
                document_title=r.title,
            )
            for r in rows
        ]

    async def keyword_search(
        self,
        db: AsyncSession,
        scope: RetrievalScope,
        query: str,
        top_k: int,
    ) -> list[SearchHit]:
        if scope.is_empty or not query.strip():
            return []
        tsv = func.to_tsvector("english", DocumentChunk.content)
        tsq = func.plainto_tsquery("english", query)
        rank = func.ts_rank(tsv, tsq)
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.collection_id,
                DocumentChunk.content,
                DocumentChunk.chunk_index,
                DocumentChunk.meta,
                rank.label("rank"),
                Document.title,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            # Same non-quarantined guard as similarity_search (see above).
            .where(tsv.op("@@")(tsq), Document.status != DocumentStatus.QUARANTINED)
            .order_by(rank.desc())
            .limit(top_k)
        )
        stmt = scope.apply(stmt)
        rows = (await db.execute(stmt)).all()
        return [
            SearchHit(
                chunk_id=r.id,
                document_id=r.document_id,
                collection_id=r.collection_id,
                content=r.content,
                chunk_index=r.chunk_index,
                metadata=r.meta or {},
                score=float(r.rank),
                document_title=r.title,
            )
            for r in rows
        ]


_store = PgVectorStore()


def get_vector_store() -> PgVectorStore:
    return _store
