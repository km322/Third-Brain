from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PlanTier

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.collection import Collection
    from app.models.team import Team
    from app.models.user import Membership


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    plan: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, native_enum=False, length=32), default=PlanTier.FREE, nullable=False
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    teams: Mapped[list[Team]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    collections: Mapped[list[Collection]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Organization {self.slug}>"
