"""Data governance surface: DLP oversharing report.

Surfaces documents that carry sensitive content (PII/confidential, as classified by the
DLP scan) AND are broadly visible (ORG/PUBLIC, whether by their own visibility override or
their collection's). Admin-only. Read-only: it reports risk; remediation is done by
changing visibility/ACLs through the existing surfaces.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, require_role
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import OrgRole, SensitivityLevel, Visibility
from app.schemas.governance import OversharingItem, OversharingReport, SensitivitySummary

router = APIRouter(prefix="/governance", tags=["governance"])

_BROAD = [Visibility.ORG, Visibility.PUBLIC]


@router.get("/oversharing", response_model=OversharingReport)
async def oversharing_report(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> OversharingReport:
    """List sensitive documents that are broadly visible, plus a sensitivity summary."""
    rows = (
        await db.execute(
            select(
                Document.id,
                Document.title,
                Document.sensitivity,
                Document.visibility,
                Collection.id,
                Collection.name,
                Collection.visibility,
            )
            .join(Collection, Collection.id == Document.collection_id)
            .where(
                Document.org_id == ctx.org_id,
                Document.sensitivity != SensitivityLevel.NONE,
                or_(
                    Document.visibility.in_(_BROAD),
                    and_(Document.visibility.is_(None), Collection.visibility.in_(_BROAD)),
                ),
            )
            .order_by(Document.sensitivity, Document.created_at.desc())
        )
    ).all()

    items = [
        OversharingItem(
            document_id=doc_id,
            title=title,
            sensitivity=sensitivity,
            effective_visibility=doc_vis or coll_vis,
            collection_id=coll_id,
            collection_name=coll_name,
        )
        for doc_id, title, sensitivity, doc_vis, coll_id, coll_name, coll_vis in rows
    ]

    counts = dict(
        (
            await db.execute(
                select(Document.sensitivity, func.count())
                .where(
                    Document.org_id == ctx.org_id,
                    Document.sensitivity != SensitivityLevel.NONE,
                )
                .group_by(Document.sensitivity)
            )
        ).all()
    )
    summary = SensitivitySummary(
        pii=counts.get(SensitivityLevel.PII, 0),
        confidential=counts.get(SensitivityLevel.CONFIDENTIAL, 0),
    )
    return OversharingReport(items=items, summary=summary, total_oversharing=len(items))
