"""Public pre-launch waitlist signups.

Unlike every other table in the app, this one is **not** org-scoped: a waitlist entry is
created by an anonymous visitor on the marketing site before any organization exists. It
is a flat, global list keyed by a unique (lower-cased) email so re-submitting the same
address is idempotent rather than creating duplicates.

Only metadata is stored - the visitor's email plus optional name/company and a small
``meta`` blob for provenance (source, referrer). No secrets, no query/document content.
"""

from __future__ import annotations

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WaitlistEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (Index("ix_waitlist_entries_created", "created_at"),)

    # Stored lower-cased + stripped so dedupe is case-insensitive; the unique index
    # enforces one row per address.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Where the signup came from (e.g. "landing", "pricing"); handy for attribution.
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WaitlistEntry {self.email}>"
