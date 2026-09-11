from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single embedded text chunk - the atomic unit of retrieval.

    ``org_id`` and ``collection_id`` are denormalized onto the chunk so that the
    permission-aware vector search can filter without extra joins.

    ``ix_document_chunks_embedding_hnsw`` is an HNSW index for fast cosine ANN search over
    the pgvector column. ``ix_document_chunks_doc_order`` is unique so a document can never
    hold two chunks at the same index - a backstop against a concurrent double-ingestion
    inserting a duplicate set of chunks (the ingestion path also serializes on a
    per-document advisory lock).
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_document_chunks_doc_order", "document_id", "chunk_index", unique=True),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    collection_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIM), nullable=True
    )
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentChunk doc={self.document_id} #{self.chunk_index}>"
