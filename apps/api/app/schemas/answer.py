"""Schemas for curated, verifiable Answers (authoritative Q&A)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import VerificationStatus, Visibility
from app.schemas.common import ORMModel


class AnswerCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1024)
    answer: str = Field(min_length=1, max_length=20000)
    collection_id: uuid.UUID | None = None
    visibility: Visibility = Visibility.ORG


class AnswerUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=1024)
    answer: str | None = Field(default=None, min_length=1, max_length=20000)
    visibility: Visibility | None = None


class AnswerVerify(BaseModel):
    review_interval_days: int | None = Field(
        default=None,
        ge=0,
        description="Days until the answer needs re-review (0 = never expires; "
        "omit for the org default).",
    )


class AnswerRead(ORMModel):
    id: uuid.UUID
    question: str
    answer: str
    collection_id: uuid.UUID | None = None
    visibility: Visibility
    verification_status: VerificationStatus
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AnswerMatch(BaseModel):
    """A verified answer surfaced alongside search results because it matches the query."""

    id: uuid.UUID
    question: str
    answer: str
    verification_status: VerificationStatus
