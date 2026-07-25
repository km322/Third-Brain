"""Verified-answer / content-freshness helpers.

Verification stamps a document or curated answer as authoritative, with an optional
review interval that computes an ``expires_at``. A periodic sweep (:func:`flag_stale`)
flips anything past its review-by date from VERIFIED to STALE so the UI can prompt a
re-review, following a "verified" + freshness workflow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.answer import Answer
from app.models.document import Document
from app.models.enums import VerificationStatus


def compute_expiry(verified_at: datetime, review_interval_days: int | None) -> datetime | None:
    """Return the review-by timestamp, or None when no interval is set."""
    if review_interval_days is None:
        return None
    return verified_at + timedelta(days=review_interval_days)


def resolve_interval(review_interval_days: int | None) -> int | None:
    """Apply the org default when the caller didn't specify an interval (0 => no expiry)."""
    if review_interval_days is None:
        return settings.DEFAULT_REVIEW_INTERVAL_DAYS
    return review_interval_days or None


async def flag_stale(db: AsyncSession) -> dict[str, int]:
    """Flip VERIFIED documents/answers past their review date to STALE. Returns counts."""
    now = datetime.now(UTC)
    doc_result = await db.execute(
        update(Document)
        .where(
            Document.verification_status == VerificationStatus.VERIFIED,
            Document.expires_at.is_not(None),
            Document.expires_at < now,
        )
        .values(verification_status=VerificationStatus.STALE)
    )
    ans_result = await db.execute(
        update(Answer)
        .where(
            Answer.verification_status == VerificationStatus.VERIFIED,
            Answer.expires_at.is_not(None),
            Answer.expires_at < now,
        )
        .values(verification_status=VerificationStatus.STALE)
    )
    await db.commit()
    return {"documents": doc_result.rowcount or 0, "answers": ans_result.rowcount or 0}
