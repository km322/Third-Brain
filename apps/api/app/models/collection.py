from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PermissionLevel, Visibility

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.organization import Organization


class Collection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge base: a named group of documents with its own embedding config
    and default visibility. Documents inherit the collection's grants unless
    overridden by a document-level ACL.
    """

    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_collection_org_slug"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    owner_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False, length=32),
        default=Visibility.PRIVATE,
        nullable=False,
    )
    default_permission: Mapped[PermissionLevel] = mapped_column(
        Enum(PermissionLevel, native_enum=False, length=32),
        default=PermissionLevel.VIEWER,
        nullable=False,
    )
    embedding_model: Mapped[str] = mapped_column(
        String(128), default=settings.EMBEDDING_MODEL, nullable=False
    )
    embedding_dim: Mapped[int] = mapped_column(
        Integer, default=settings.EMBEDDING_DIM, nullable=False
    )
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="collections")
    documents: Mapped[list[Document]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Collection {self.slug} org={self.org_id}>"
