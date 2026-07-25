"""Search + RAG chat endpoints.

* ``POST /search`` - permission-aware retrieval, returns ranked citations.
* ``POST /search/chat`` - retrieval-augmented answer. When ``stream=true`` the response
  is a ``text/event-stream`` of answer tokens (the dashboard's ``streamChat`` appends
  each decoded chunk directly); otherwise a JSON ``{answer, citations}`` body.

Authorization is delegated entirely to the retrieval scope: the permission engine
already restricts results to chunks the caller may view, so these routes add no further
gating beyond per-API-key rate limiting. ``POST /search`` records a SEARCH usage record;
``POST /search/chat`` is metered as a COMPLETION by :mod:`app.services.rag` (plus an
EMBEDDING for its retrieval) and is NOT also counted as a search. Both write a
``search.performed`` audit entry, then commit.
"""

from __future__ import annotations

import contextlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, enforce_rate_limit, require_scope
from app.core.logging import get_logger
from app.models.enums import AuditAction, MessageRole, QueryKind, UsageKind
from app.schemas.answer import AnswerMatch
from app.schemas.search import (
    ChatRequest,
    ChatResponse,
    Citation,
    SearchRequest,
    SearchResponse,
    WebSource,
)
from app.services import rag, retrieval
from app.services.answers import matching_answers
from app.services.conversations import append_message, get_owned_conversation, history_messages
from app.services.feedback import record_query_insight
from app.services.metering import record_audit, record_usage
from app.services.vectorstore import SearchHit
from app.services.web_search import web_search

router = APIRouter(prefix="/search", tags=["search"])
logger = get_logger(__name__)

_SNIPPET_LEN = 240


def _to_citation(hit: SearchHit) -> Citation:
    """Project a retrieval hit into the public citation shape with a short snippet."""
    content = (hit.content or "").strip()
    snippet = content[:_SNIPPET_LEN].rstrip()
    if len(content) > _SNIPPET_LEN:
        snippet += "…"
    return Citation(
        document_id=hit.document_id,
        document_title=hit.document_title or "Untitled",
        collection_id=hit.collection_id,
        chunk_index=hit.chunk_index,
        score=round(float(hit.score), 6),
        snippet=snippet,
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("search")),
) -> SearchResponse:
    """Retrieve the most relevant, caller-visible chunks for a query."""
    await enforce_rate_limit(ctx)

    top_k = payload.top_k or settings.RETRIEVAL_TOP_K
    started = time.perf_counter()
    hits = await retrieval.retrieve(
        db,
        ctx,
        payload.query,
        top_k=payload.top_k,
        collection_ids=payload.collection_ids,
        hybrid=payload.hybrid,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    citations = [_to_citation(h) for h in hits]
    top_score = max((float(h.score) for h in hits), default=None)

    audit_meta = {
        "query": payload.query[:500],
        "top_k": top_k,
        "hybrid": payload.hybrid,
        "hits": len(citations),
    }
    insight_id = await record_query_insight(
        db, ctx, QueryKind.SEARCH, query=payload.query, result_count=len(hits), top_score=top_score
    )
    await record_usage(db, ctx, UsageKind.SEARCH, units=1, latency_ms=duration_ms, meta=audit_meta)
    await record_audit(
        db,
        ctx,
        AuditAction.SEARCH_PERFORMED.value,
        resource_type="search",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        meta=audit_meta,
    )
    await db.commit()

    matched = await matching_answers(db, ctx, payload.query)
    answers = [
        AnswerMatch(
            id=a.id,
            question=a.question,
            answer=a.answer,
            verification_status=a.verification_status,
        )
        for a in matched
    ]
    return SearchResponse(
        query=payload.query, hits=citations, answers=answers, insight_id=insight_id
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_scope("search")),
):
    """Answer a question from the caller's knowledge base, grounded in citations."""
    await enforce_rate_limit(ctx)

    top_k = payload.top_k or settings.RETRIEVAL_TOP_K
    ip = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    # Resolve conversation history + optional web grounding once, shared by both paths.
    conv = None
    history = None
    if payload.conversation_id is not None:
        conv = await get_owned_conversation(db, ctx, payload.conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        history = await history_messages(db, conv.id)
    web_results = await web_search(payload.query) if payload.web else []
    web_sources = [WebSource(title=w.title, url=w.url, snippet=w.snippet) for w in web_results]

    if payload.stream:

        async def token_stream():
            # SSE JSON frames: {"type":"token","text":...} per delta, a final
            # {"type":"citations","citations":[...]} carrying the passages the answer was
            # grounded in, then [DONE]. Streaming the citations here lets the client render
            # the Sources panel from this one retrieval instead of firing a second, fully
            # metered /search - which double-counted every Ask.
            cited: list[SearchHit] = []
            answer_parts: list[str] = []
            try:
                # aclosing propagates a mid-stream disconnect down through rag.stream_answer to
                # the inner LLM generator, so their finally blocks (metering, span finalization)
                # run promptly instead of at GC time.
                async with contextlib.aclosing(
                    rag.stream_answer(
                        db,
                        ctx,
                        payload.query,
                        collection_ids=payload.collection_ids,
                        top_k=payload.top_k,
                        model=payload.model,
                        citations_out=cited,
                        history=history,
                        web_results=web_results,
                    )
                ) as answer_stream:
                    async for delta in answer_stream:
                        answer_parts.append(delta)
                        yield f"data: {json.dumps({'type': 'token', 'text': delta})}\n\n"
                insight_id = await record_query_insight(
                    db,
                    ctx,
                    QueryKind.CHAT,
                    query=payload.query,
                    result_count=len(cited),
                    top_score=max((float(h.score) for h in cited), default=None),
                )
                citations = [_to_citation(h).model_dump(mode="json") for h in cited]
                if conv is not None:
                    await append_message(db, conv, MessageRole.USER, payload.query)
                    await append_message(
                        db, conv, MessageRole.ASSISTANT, "".join(answer_parts), citations=citations
                    )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "citations",
                            "citations": citations,
                            "insight_id": str(insight_id),
                            "conversation_id": str(conv.id) if conv else None,
                            "web_sources": [w.model_dump() for w in web_sources],
                        }
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"
            finally:
                # Finalize in a ``finally`` so a mid-stream client disconnect still audits
                # and commits the usage already recorded (EMBEDDING during retrieval and the
                # COMPLETION metered in ``rag.stream_answer``'s own finally) rather than
                # rolling it back when the session closes - otherwise streams can be aborted
                # to evade metering. The answer is metered as a COMPLETION, not a SEARCH, so
                # recording a SEARCH here too would double-count each Ask.
                try:
                    meta = {"query": payload.query[:500], "top_k": top_k, "streamed": True}
                    await record_audit(
                        db,
                        ctx,
                        AuditAction.SEARCH_PERFORMED.value,
                        resource_type="search_chat",
                        ip_address=ip,
                        user_agent=user_agent,
                        meta=meta,
                    )
                    await db.commit()
                except Exception as exc:
                    logger.warning("Failed to finalize streamed chat metering: %s", exc)

        return StreamingResponse(
            token_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    answer_text, cited = await rag.answer(
        db,
        ctx,
        payload.query,
        collection_ids=payload.collection_ids,
        top_k=payload.top_k,
        model=payload.model,
        history=history,
        web_results=web_results,
    )
    citations = [_to_citation(h) for h in cited]

    # The answer is metered as a COMPLETION by ``rag.answer`` (retrieval as EMBEDDING), so
    # we audit but don't also record a SEARCH, which would double-count the Ask.
    meta = {
        "query": payload.query[:500],
        "top_k": top_k,
        "citations": len(citations),
    }
    insight_id = await record_query_insight(
        db,
        ctx,
        QueryKind.CHAT,
        query=payload.query,
        result_count=len(cited),
        top_score=max((float(h.score) for h in cited), default=None),
    )
    if conv is not None:
        await append_message(db, conv, MessageRole.USER, payload.query)
        await append_message(
            db,
            conv,
            MessageRole.ASSISTANT,
            answer_text,
            citations=[c.model_dump(mode="json") for c in citations],
        )
    await record_audit(
        db,
        ctx,
        AuditAction.SEARCH_PERFORMED.value,
        resource_type="search_chat",
        ip_address=ip,
        user_agent=user_agent,
        meta=meta,
    )
    await db.commit()

    return ChatResponse(
        answer=answer_text,
        citations=citations,
        insight_id=insight_id,
        conversation_id=conv.id if conv else None,
        web_sources=web_sources,
    )
