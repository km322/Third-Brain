"""Answer feedback + knowledge-gap analytics.

- ``POST /feedback`` - any member attaches a thumbs up/down to a prior query insight.
- ``GET /feedback/gaps`` - admin knowledge-gap report (aggregates + optional query samples).
- ``GET/PUT /feedback/retention`` - admin toggle for storing raw query text (off by default).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context, require_role
from app.models.enums import OrgRole
from app.schemas.common import Message
from app.schemas.feedback import FeedbackCreate, KnowledgeGapReport, RetentionSetting
from app.services.feedback import (
    attach_feedback,
    knowledge_gap_report,
    retention_enabled,
    set_retention,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=Message)
async def submit_feedback(
    payload: FeedbackCreate,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> Message:
    """Attach a rating to a prior search/chat result."""
    insight = await attach_feedback(db, ctx, payload.insight_id, payload.rating, payload.reason)
    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Query insight not found")
    await db.commit()
    return Message(detail="Feedback recorded")


@router.get("/gaps", response_model=KnowledgeGapReport)
async def knowledge_gaps(
    days: int = 30,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeGapReport:
    """Knowledge-gap report: zero-result and thumbs-down queries over a window."""
    days = max(1, min(days, 365))
    report = await knowledge_gap_report(db, ctx, days=days)
    return KnowledgeGapReport(**report)


@router.get("/retention", response_model=RetentionSetting)
async def get_retention(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> RetentionSetting:
    return RetentionSetting(enabled=await retention_enabled(db, ctx.org_id))


@router.put("/retention", response_model=RetentionSetting)
async def update_retention(
    payload: RetentionSetting,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> RetentionSetting:
    """Enable/disable storing raw query text for the knowledge-gap report (off by default)."""
    await set_retention(db, ctx.org_id, payload.enabled)
    await db.commit()
    return RetentionSetting(enabled=payload.enabled)
