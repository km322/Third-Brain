from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UsageKind


class UsageRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One metered operation (embed/complete/search/ingest).

    Recorded for analytics and provider cost attribution: ``cost_usd`` is what the
    operator's own provider keys were charged, never a charge from Third Brain.
    """

    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_org_time", "org_id", "created_at"),
        Index("ix_usage_org_kind_time", "org_id", "kind", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[UsageKind] = mapped_column(
        Enum(UsageKind, native_enum=False, length=32), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
