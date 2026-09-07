"""Vector store abstraction.

pgvector is the default implementation. The interface is intentionally small so a
hosted store (Qdrant, Pinecone, Milvus) can be dropped in "for speed" without touching
retrieval code - as long as it can honor the permission ``RetrievalScope``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.permissions import RetrievalScope


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    content: str
    score: float
    chunk_index: int = 0
    metadata: dict = field(default_factory=dict)
    document_title: str | None = None


class VectorStore(Protocol):
    async def similarity_search(
        self,
        db: AsyncSession,
        scope: RetrievalScope,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchHit]: ...

    async def keyword_search(
        self,
        db: AsyncSession,
        scope: RetrievalScope,
        query: str,
        top_k: int,
    ) -> list[SearchHit]: ...
