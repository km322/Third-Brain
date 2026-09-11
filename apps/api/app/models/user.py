from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MembershipStatus, OrgRole

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.team import TeamMember


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person who can sign in, independent of any organization.

    ``ix_users_email_lower`` is the unique functional index backing the case-insensitive
    lookup in ``auth_service.get_user_by_email`` (``lower(email) = $1``). It is created by
    the baseline migration; the raw ``ix_users_email`` on the column itself stays.
    """

    __tablename__ = "users"

    __table_args__ = (Index("ix_users_email_lower", text("lower(email)"), unique=True),)

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    """Embedded in every JWT as ``ver`` and re-checked on each request; bumping it on
    credential events (password change/reset) revokes all outstanding sessions."""

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Membership.user_id",
    )
    """``foreign_keys`` is explicit because Membership has two FKs to users (user_id,
    invited_by_id) and they must be disambiguated."""
    team_links: Mapped[list[TeamMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's membership of an organization, carrying their org-level role.

    ``ix_memberships_org_created`` is the composite backing the ``list_members`` per-org,
    oldest-first ordering.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),
        Index("ix_memberships_org_created", "org_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, native_enum=False, length=32), default=OrgRole.VIEWER, nullable=False
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, native_enum=False, length=32),
        default=MembershipStatus.ACTIVE,
        nullable=False,
    )
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Membership user={self.user_id} org={self.org_id} role={self.role}>"
