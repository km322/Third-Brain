from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PermissionLevel, PrincipalType, ResourceType

if TYPE_CHECKING:
    pass


class AccessGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An explicit ACL entry: principal → resource → permission level.

    A grant's ``resource_id`` references either a collection or a document
    (discriminated by ``resource_type``). Principals are users or teams.
    """

    __tablename__ = "access_grants"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "principal_type",
            "principal_id",
            name="uq_grant_resource_principal",
        ),
        Index("ix_grant_lookup", "resource_type", "resource_id"),
        Index("ix_grant_principal", "principal_type", "principal_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    resource_type: Mapped[ResourceType] = mapped_column(
        Enum(ResourceType, native_enum=False, length=32), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    principal_type: Mapped[PrincipalType] = mapped_column(
        Enum(PrincipalType, native_enum=False, length=32), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    permission: Mapped[PermissionLevel] = mapped_column(
        Enum(PermissionLevel, native_enum=False, length=32),
        default=PermissionLevel.VIEWER,
        nullable=False,
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True, nullable=True
    )
    """Non-null when this grant was materialised by a data-source ACL sync. A re-sync only
    ever adds/removes grants carrying its own ``source_id``; manually-created grants
    (``source_id IS NULL``) are never touched. ON DELETE CASCADE so removing a data source
    cleans up exactly the grants it created."""

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AccessGrant {self.principal_type}:{self.principal_id} "
            f"{self.permission} on {self.resource_type}:{self.resource_id}>"
        )
