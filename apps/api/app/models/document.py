from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    DocumentStatus,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
    Visibility,
)

if TYPE_CHECKING:
    from app.models.chunk import DocumentChunk
    from app.models.collection import Collection


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An ingested source document plus the governance state attached to it.

    Why each entry in ``__table_args__`` exists, in declaration order:

    * ``ix_documents_org_visibility`` - partial index for the visibility-override lookup
      in ``permissions.build_retrieval_scope`` (rows whose visibility is not inherited).
    * ``ix_documents_org_created`` - composite backing the ``list_documents`` newest-first
      ordering.
    * ``ix_documents_meta_file_token`` - capability-token lookup for GET /files/{token}, an
      unauthenticated public endpoint, so the probe must be an index hit, never a full scan.
    * ``uq_documents_source_external`` - a synced document is uniquely identified by
      (data source, upstream id); the partial unique index makes re-sync an idempotent
      upsert.
    """

    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ix_documents_org_visibility",
            "org_id",
            postgresql_where=text("visibility IS NOT NULL"),
        ),
        Index("ix_documents_org_created", "org_id", text("created_at DESC")),
        Index("ix_documents_meta_file_token", text("(metadata ->> 'file_token')")),
        Index(
            "uq_documents_source_external",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=32), default=SourceType.TEXT, nullable=False
    )
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True, nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    """Data-source linkage (feature: connectors). ``source_id`` is set when the document was
    synced from a DataSource; (source_id, external_id) is the natural key for idempotent
    re-sync. Deleting the data source removes the documents it created."""
    visibility: Mapped[Visibility | None] = mapped_column(
        Enum(Visibility, native_enum=False, length=32), nullable=True
    )
    """NULL means "inherit from collection"."""
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=32),
        default=DocumentStatus.PENDING,
        index=True,
        nullable=False,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=16),
        default=VerificationStatus.UNVERIFIED,
        index=True,
        nullable=False,
    )
    """Verified answers / content freshness (feature: verification)."""
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    """When the current verification goes stale (verified_at + review_interval_days)."""

    sensitivity: Mapped[SensitivityLevel] = mapped_column(
        Enum(SensitivityLevel, native_enum=False, length=16),
        default=SensitivityLevel.NONE,
        index=True,
        nullable=False,
    )
    """DLP classification (feature: PII/DLP). NONE unless the sensitivity scan hit."""

    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    collection: Mapped[Collection] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document {self.title!r} status={self.status}>"
