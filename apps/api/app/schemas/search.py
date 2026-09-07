"""Request/response schemas for the search + RAG chat surface.

These mirror the frontend ``Citation``, ``SearchResult`` and ``ChatResponse`` types
(``apps/web/lib/types.ts``) exactly, so the API and dashboard stay in lockstep.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.schemas.answer import AnswerMatch


class SearchRequest(BaseModel):
    """Body for ``POST /search`` - permission-aware retrieval only."""

    query: str = Field(..., min_length=1, max_length=8192)
    collection_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Optional narrowing to specific collections. Omit to search "
        "every collection the caller can view.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Number of hits to return. Defaults to settings.RETRIEVAL_TOP_K.",
    )
    hybrid: bool = Field(
        default=True,
        description="Fuse vector + keyword search via Reciprocal Rank Fusion.",
    )

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


class ChatRequest(BaseModel):
    """Body for ``POST /search/chat`` - retrieval-augmented generation."""

    query: str = Field(..., min_length=1, max_length=8192)
    collection_ids: list[uuid.UUID] | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    model: str | None = Field(
        default=None,
        description="Override the completion model. Defaults to settings.DEFAULT_COMPLETION_MODEL.",
    )
    stream: bool = Field(
        default=False,
        description="When true the response is a text/event-stream of answer tokens.",
    )
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="Continue a multi-turn conversation; prior turns ground the answer.",
    )
    web: bool = Field(
        default=False,
        description="Also ground the answer in web-search results (if a provider is configured).",
    )

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


class Citation(BaseModel):
    """A retrieved chunk surfaced to the caller as an attributable source."""

    document_id: uuid.UUID
    document_title: str
    collection_id: uuid.UUID
    chunk_index: int
    score: float
    snippet: str


class SearchResponse(BaseModel):
    """Response for ``POST /search``."""

    query: str
    hits: list[Citation]
    # Verified curated answers whose question matches the query, surfaced above raw hits.
    answers: list[AnswerMatch] = Field(default_factory=list)
    # Id of the recorded query insight; submit to POST /feedback to rate this result.
    insight_id: uuid.UUID | None = None


class WebSource(BaseModel):
    """An external web result used to ground the answer."""

    title: str
    url: str
    snippet: str


class ChatResponse(BaseModel):
    """Non-streaming response for ``POST /search/chat``."""

    answer: str
    citations: list[Citation]
    insight_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    web_sources: list[WebSource] = Field(default_factory=list)
