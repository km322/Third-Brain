"""Answer feedback + knowledge-gap analytics.

Every search/chat writes a :class:`QueryInsight` (metadata only). A user can attach a
thumbs up/down to it later. The knowledge-gap report aggregates these into scale-independent
signals - zero-result queries and thumbs-down queries are the gaps - without ever requiring
the query text. Raw query text is stored ONLY when the org opts in
(``organization.settings['retain_query_text']``), honouring the no-content-logging invariant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.enums import FeedbackRating, QueryKind
from app.models.feedback import QueryInsight
from app.models.organization import Organization

RETAIN_QUERY_TEXT_KEY = "retain_query_text"
_MAX_GAP_SAMPLES = 20


async def retention_enabled(db: AsyncSession, org_id: uuid.UUID) -> bool:
    org = await db.get(Organization, org_id)
    return bool(org and (org.settings or {}).get(RETAIN_QUERY_TEXT_KEY))


async def set_retention(db: AsyncSession, org_id: uuid.UUID, enabled: bool) -> None:
    org = await db.get(Organization, org_id)
    if org is None:
        return
    settings_dict = dict(org.settings or {})
    settings_dict[RETAIN_QUERY_TEXT_KEY] = enabled
    org.settings = settings_dict


async def record_query_insight(
    db: AsyncSession,
    ctx: AuthContext,
    kind: QueryKind,
    *,
    query: str,
    result_count: int,
    top_score: float | None,
) -> uuid.UUID:
    """Record a query's outcome and return the insight id (the caller commits)."""
    retain = await retention_enabled(db, ctx.org_id)
    insight = QueryInsight(
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        kind=kind,
        result_count=result_count,
        top_score=top_score,
        answered=result_count > 0,
        query_text=query[:2000] if retain else None,
    )
    db.add(insight)
    await db.flush()
    return insight.id


async def attach_feedback(
    db: AsyncSession,
    ctx: AuthContext,
    insight_id: uuid.UUID,
    rating: FeedbackRating,
    reason: str | None,
) -> QueryInsight | None:
    """Attach a rating to a prior query insight (org-scoped). None when not found."""
    insight = await db.get(QueryInsight, insight_id)
    if insight is None or insight.org_id != ctx.org_id:
        return None
    insight.rating = rating
    insight.reason = reason[:512] if reason else None
    insight.rated_at = datetime.now(UTC)
    return insight


async def knowledge_gap_report(db: AsyncSession, ctx: AuthContext, *, days: int = 30) -> dict:
    """Aggregate query outcomes over the last ``days`` into a knowledge-gap summary."""
    since = datetime.now(UTC) - timedelta(days=days)
    scope = (QueryInsight.org_id == ctx.org_id, QueryInsight.created_at >= since)

    async def _count(*extra) -> int:
        return (
            await db.execute(select(func.count()).select_from(QueryInsight).where(*scope, *extra))
        ).scalar_one()

    total = await _count()
    unanswered = await _count(QueryInsight.answered.is_(False))
    negative = await _count(QueryInsight.rating == FeedbackRating.DOWN)
    positive = await _count(QueryInsight.rating == FeedbackRating.UP)
    answered = total - unanswered

    top_gaps: list[str] = []
    if await retention_enabled(db, ctx.org_id) and (unanswered or negative):
        rows = (
            (
                await db.execute(
                    select(QueryInsight.query_text)
                    .where(
                        *scope,
                        QueryInsight.query_text.is_not(None),
                        or_(
                            QueryInsight.answered.is_(False),
                            QueryInsight.rating == FeedbackRating.DOWN,
                        ),
                    )
                    .order_by(QueryInsight.created_at.desc())
                    .limit(_MAX_GAP_SAMPLES)
                )
            )
            .scalars()
            .all()
        )
        top_gaps = list(dict.fromkeys(rows))

    return {
        "window_days": days,
        "total_queries": total,
        "answered": answered,
        "unanswered": unanswered,
        "positive": positive,
        "negative": negative,
        "answered_rate": round(answered / total, 4) if total else None,
        "query_text_retained": await retention_enabled(db, ctx.org_id),
        "top_gaps": top_gaps,
    }
