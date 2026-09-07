"""Named entities extracted from documents during ingestion (NER enrichment).

Entities (people, orgs, products, projects, …) are deduped per org by normalized name
+ kind, linked to the documents that mention them. They power entity filters in search
and entity edges in the knowledge graph. Access to an entity's documents is always
re-checked through the permission engine; the entity index itself only records mentions.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EntityKind

if TYPE_CHECKING:
    pass


class Entity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("org_id", "kind", "normalized", name="uq_entity_org_kind_norm"),
        # The kind-pin lookup filters on (org_id, normalized); the unique index above
        # cannot serve it because ``kind`` sits between the two columns.
        Index("ix_entities_org_normalized", "org_id", "normalized"),
        Index("ix_entities_org_kind", "org_id", "kind"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[EntityKind] = mapped_column(
        Enum(EntityKind, native_enum=False, length=16), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized: Mapped[str] = mapped_column(String(512), nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entity {self.kind}:{self.name!r}>"


class DocumentEntity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_entities"
    __table_args__ = (UniqueConstraint("document_id", "entity_id", name="uq_document_entity"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True, nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentEntity doc={self.document_id} entity={self.entity_id}>"
