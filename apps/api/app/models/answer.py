"""Curated, authoritative Q&A ("Answers").

A hand-written question/answer pair that a manager can mark verified. When a search
query closely matches a verified answer's question, the answer is surfaced above raw
retrieval as the authoritative response. Permission is governed exactly like a
collection/document: by ``visibility`` (+ optional ``collection_id`` scoping) resolved
through the same permission engine.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import VerificationStatus, Visibility

if TYPE_CHECKING:
    pass


class Answer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_org_status", "org_id", "verification_status"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Optional scoping: NULL => org-wide (governed by ``visibility``); set => also
    # requires viewer access to the collection.
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(String(1024), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False, length=32),
        default=Visibility.ORG,
        nullable=False,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=16),
        default=VerificationStatus.UNVERIFIED,
        nullable=False,
    )
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Answer {self.question[:40]!r} {self.verification_status}>"
