"""Per-query quality signal for answer feedback + knowledge-gap analytics.

One row is written per search/chat with implicit quality signals (result count, best
score, whether it was answerable). The user can later attach an explicit rating
(thumbs up/down) referencing the same row. Aggregations over these rows power the
knowledge-gap report (zero-result / low-confidence / negatively-rated queries).

Raw ``query_text`` is stored **only** when the org opts in
(``organization.settings['retain_query_text']``), honouring the "metadata only" logging
invariant; the analytics themselves never require the text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import FeedbackRating, QueryKind

if TYPE_CHECKING:
    pass


class QueryInsight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "query_insights"
    __table_args__ = (
        Index("ix_query_insights_org_created", "org_id", "created_at"),
        Index("ix_query_insights_org_rating", "org_id", "rating"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[QueryKind] = mapped_column(
        Enum(QueryKind, native_enum=False, length=16), nullable=False
    )
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Whether the query produced a usable result (>=1 hit above the confidence floor, or a
    # grounded chat answer). ``False`` rows are the knowledge gaps.
    answered: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rating: Mapped[FeedbackRating | None] = mapped_column(
        Enum(FeedbackRating, native_enum=False, length=8), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Present only under opt-in retention (see module docstring).
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QueryInsight {self.kind} answered={self.answered} rating={self.rating}>"
