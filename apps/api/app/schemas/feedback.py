"""Schemas for answer feedback + knowledge-gap analytics."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import FeedbackRating


class FeedbackCreate(BaseModel):
    insight_id: uuid.UUID
    rating: FeedbackRating
    reason: str | None = Field(default=None, max_length=512)


class KnowledgeGapReport(BaseModel):
    window_days: int
    total_queries: int
    answered: int
    unanswered: int
    positive: int
    negative: int
    answered_rate: float | None = None
    query_text_retained: bool
    top_gaps: list[str] = Field(default_factory=list)
    """Populated only when the org has opted into query-text retention."""


class RetentionSetting(BaseModel):
    enabled: bool
