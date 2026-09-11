"""Permission-aware retrieval - the read side of the knowledge base.

``retrieve`` is the single entry point used by both plain search (``POST /search``)
and RAG chat (``rag.answer`` / ``rag.stream_answer``). It:

1. Embeds the query once (cached in Redis by model + query hash, so repeated or
   paginated searches never re-pay for the same embedding).
2. Resolves the caller's visible chunk universe via the permission engine
   (:func:`build_retrieval_scope`) - a chunk the caller cannot view can never enter
   a result set and therefore never a prompt.
3. Runs vector similarity search and, in hybrid mode, keyword search, then fuses the
   two rankings with Reciprocal Rank Fusion (RRF) and de-duplicates by chunk id.

The embedding provider call is metered as ``EMBEDDING`` usage (flush-only) on a cache
miss; the calling request commits. Cache hits incur no provider cost and are not
metered.
"""

from __future__ import annotations

import hashlib
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.core.redis import cache_get_json, cache_set_json
from app.core.telemetry import get_tracer
from app.models.enums import ConnectorPurpose, UsageKind
from app.services.llm import effective_provider, embed_texts, is_offline, resolver
from app.services.llm.pricing import embedding_cost, is_billable_provider
from app.services.metering import record_usage
from app.services.permissions import build_retrieval_scope
from app.services.vectorstore import SearchHit, get_vector_store

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_RRF_K = 60
"""Reciprocal Rank Fusion constant. 60 is the value from the original Cormack et al.
paper and works well without tuning."""


def _embedding_cache_key(
    provider: str, model: str, api_base: str | None, query: str, *, offline: bool
) -> str:
    """The Redis key a query's embedding is cached under.

    The cache key is scoped by (provider, model, endpoint): two orgs whose connectors
    share a model name but point at different providers must never read each other's cached
    vectors, while orgs on the same platform endpoint still share the cache. The wire
    ``provider`` segment keeps e.g. an OpenAI-served and a Gemini-served vector for the
    same configured model name apart. The ``offline``/``live`` tag additionally keeps
    deterministic offline STUB vectors out of the live-vector cache (and vice versa), so a
    keyless/offline org and a real-provider org on the same (model, endpoint) cannot
    poison each other's embeddings.
    """
    kind = "offline" if offline else "live"
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return f"emb:{kind}:{provider}:{model}:{api_base or 'default'}:{digest}"


async def _embed_query(db: AsyncSession, ctx: AuthContext, query: str) -> tuple[list[float], bool]:
    """Return the query embedding and whether it was a cache hit.

    The query is embedded through the SAME provider/model the org indexes with - its
    default embedding connector (or the platform default when none is configured) - so
    query and chunk vectors always live in the same embedding space. On a cache miss the
    provider call is recorded as EMBEDDING usage (flush-only; the request commits). Redis
    failures degrade gracefully to a direct embed so search never hard-depends on the cache.
    Returns ``(vector, cache_hit)`` so ``retrieve`` can log the outcome.

    Cost is charged only for a real provider call; the deterministic offline provider is
    free.
    """
    with tracer.start_as_current_span("retrieval.embed_query") as span:
        res = await resolver.resolve(db, ctx.org_id, ConnectorPurpose.EMBEDDING)
        model = res.model or settings.EMBEDDING_MODEL
        key = _embedding_cache_key(
            effective_provider(res.provider, purpose="embedding"),
            model,
            res.api_base,
            query,
            offline=is_offline(res.api_key, res.api_base, res.provider),
        )

        try:
            cached = await cache_get_json(key)
            if isinstance(cached, list) and cached:
                span.set_attribute("app.cache_hit", True)
                return [float(x) for x in cached], True
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("Embedding cache read failed: %s", exc)
        span.set_attribute("app.cache_hit", False)

        result = await embed_texts(
            [query],
            model=model,
            api_key=res.api_key,
            api_base=res.api_base,
            provider=res.provider,
        )
        vector = result.vectors[0]

        cost = (
            embedding_cost(result.model, result.tokens)
            if is_billable_provider(result.provider)
            else 0.0
        )
        await record_usage(
            db,
            ctx,
            UsageKind.EMBEDDING,
            provider=result.provider,
            model=result.model,
            tokens_in=result.tokens,
            units=1,
            cost_usd=cost,
            latency_ms=result.latency_ms,
            meta={"feature": "search_query"},
        )

        try:
            await cache_set_json(key, vector, settings.EMBED_CACHE_TTL_SECONDS)
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("Embedding cache write failed: %s", exc)

        return vector, False


def _reciprocal_rank_fusion(ranked_lists: list[list[SearchHit]], top_k: int) -> list[SearchHit]:
    """Fuse several best-first ranked hit lists into one.

    Each list contributes ``1 / (k + rank)`` to a chunk's fused score. Chunks are
    de-duplicated by ``chunk_id``; the surviving hit object carries a **normalized** fused
    score in ``[0, 1]`` so downstream callers (and the UI's "N% match") see a comparable
    relevance number. Without normalization the raw RRF sum tops out near
    ``len(lists) / (_RRF_K + 1)`` (~0.03), which reads as "no match" everywhere.

    The max attainable score is rank 0 in every provided list; dividing by it maps the top
    possible relevance to 1.0 while preserving the fused ranking exactly.
    """
    fused_scores: dict[uuid.UUID, float] = {}
    hits_by_id: dict[uuid.UUID, SearchHit] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked):
            fused_scores[hit.chunk_id] = fused_scores.get(hit.chunk_id, 0.0) + 1.0 / (
                _RRF_K + rank + 1
            )
            hits_by_id.setdefault(hit.chunk_id, hit)

    max_attainable = len(ranked_lists) / (_RRF_K + 1) if ranked_lists else 1.0
    ordered = sorted(hits_by_id.values(), key=lambda h: fused_scores[h.chunk_id], reverse=True)
    for hit in ordered:
        hit.score = min(1.0, fused_scores[hit.chunk_id] / max_attainable)
    return ordered[:top_k]


async def retrieve(
    db: AsyncSession,
    ctx: AuthContext,
    query: str,
    top_k: int | None = None,
    collection_ids: list[uuid.UUID] | None = None,
    hybrid: bool = True,
) -> list[SearchHit]:
    """Retrieve the most relevant, caller-visible chunks for ``query``.

    Args:
        db: Async session (the request commits any flushed usage records).
        ctx: The authenticated caller; determines the retrieval scope.
        query: Natural-language search string.
        top_k: Max hits to return; defaults to ``settings.RETRIEVAL_TOP_K``.
        collection_ids: Optional narrowing to specific collections. Collections the
            caller cannot view are silently dropped by the permission scope.
        hybrid: When true, fuse vector + keyword search with RRF; otherwise vector only.

    Returns:
        A best-first list of :class:`SearchHit`, at most ``top_k`` long. Empty when the
        caller has access to nothing (or the requested collections are all invisible).

    The visible-chunk predicate is resolved first: if the caller can see nothing there is
    no point paying for an embedding.

    In vector-only mode cosine similarity is surfaced as a [0, 1] relevance (distance can
    exceed 1 for near-opposite vectors, which would otherwise render as a negative "%
    match"). In hybrid mode each retriever is over-fetched so RRF has enough overlap to work
    with and the result is then trimmed, and whatever retrievers returned hits (one or both)
    are fused, so the surfaced score is always the normalized, comparable RRF relevance -
    never a raw cosine similarity or ts_rank on one path and a fused score on another.
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    started = time.perf_counter()

    with tracer.start_as_current_span("retrieval.retrieve") as span:
        span.set_attribute("app.top_k", top_k)
        span.set_attribute("app.hybrid", hybrid)
        span.set_attribute("app.collection_count", len(collection_ids) if collection_ids else 0)

        with tracer.start_as_current_span("retrieval.scope"):
            scope = await build_retrieval_scope(db, ctx, collection_ids=collection_ids)

        cache_hit: bool | None = None
        if scope.is_empty:
            hits: list[SearchHit] = []
        else:
            store = get_vector_store()
            query_embedding, cache_hit = await _embed_query(db, ctx, query)

            if not hybrid:
                with tracer.start_as_current_span("retrieval.vector_search") as search_span:
                    hits = await store.similarity_search(db, scope, query_embedding, top_k)
                    search_span.set_attribute("app.result_count", len(hits))
                for hit in hits:
                    hit.score = max(0.0, min(1.0, hit.score))
            else:
                candidate_k = min(max(top_k * 3, top_k), 50)
                with tracer.start_as_current_span("retrieval.vector_search") as search_span:
                    vector_hits = await store.similarity_search(
                        db, scope, query_embedding, candidate_k
                    )
                    search_span.set_attribute("app.result_count", len(vector_hits))
                with tracer.start_as_current_span("retrieval.keyword_search") as search_span:
                    keyword_hits = await store.keyword_search(db, scope, query, candidate_k)
                    search_span.set_attribute("app.result_count", len(keyword_hits))

                present = [lst for lst in (vector_hits, keyword_hits) if lst]
                hits = _reciprocal_rank_fusion(present, top_k) if present else []

        span.set_attribute("app.result_count", len(hits))
        logger.info(
            "retrieval",
            top_k=top_k,
            hybrid=hybrid,
            hits=len(hits),
            cache_hit=cache_hit,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return hits
