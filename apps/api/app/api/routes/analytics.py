"""Analytics endpoints: organization KPIs, usage time-series and the audit log.

All queries are strictly scoped to ``ctx.org_id`` and use SQL-side aggregation
(``func.count`` / ``func.sum`` / ``func.date``) rather than pulling rows into
Python. Every endpoint is read-only, so no ``db.commit`` is issued.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, require_role, require_scope
from app.models.api_key import ApiKey
from app.models.audit import AuditLog
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import OrgRole, UsageKind
from app.models.usage import UsageRecord
from app.models.user import Membership, User
from app.schemas.analytics import (
    AnalyticsOverview,
    AuditLogEntry,
    UsageKindBreakdown,
    UsagePoint,
    UsageSummary,
)
from app.schemas.common import Page, PaginationParams

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _as_date(value: Any) -> date:
    """Normalize a DB ``func.date`` result (date / datetime / ISO string) to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@router.get("/overview", response_model=AnalyticsOverview)
async def get_overview(
    ctx: AuthContext = Depends(require_scope("manage")),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOverview:
    """Headline counts plus recent search and token/cost activity for the org.

    Exposes org-wide token spend and USD cost, so API-key callers must hold the ``manage``
    scope; human sessions carry an implicit wildcard and see it as part of the dashboard.
    """
    now = datetime.now(UTC)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    async def _count(model: Any, *conditions: Any) -> int:
        stmt = select(func.count()).select_from(model).where(model.org_id == ctx.org_id)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(await db.scalar(stmt) or 0)

    documents = await _count(Document)
    collections = await _count(Collection)
    members = await _count(Membership)
    api_keys = await _count(ApiKey, ApiKey.revoked.is_(False))

    searches_7d = int(
        await db.scalar(
            select(func.count())
            .select_from(UsageRecord)
            .where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.kind == UsageKind.SEARCH,
                UsageRecord.created_at >= cutoff_7d,
            )
        )
        or 0
    )

    tokens_30d, cost_30d = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            ).where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.created_at >= cutoff_30d,
            )
        )
    ).one()

    return AnalyticsOverview(
        documents=documents,
        collections=collections,
        members=members,
        api_keys=api_keys,
        searches_7d=searches_7d,
        tokens_30d=int(tokens_30d or 0),
        cost_30d=round(float(cost_30d or 0.0), 6),
    )


@router.get("/usage", response_model=UsageSummary)
async def get_usage(
    days: int = Query(default=30, ge=1, le=365, description="Window size in days."),
    ctx: AuthContext = Depends(require_scope("manage")),
    db: AsyncSession = Depends(get_db),
) -> UsageSummary:
    """Aggregate metered usage over the last ``days`` days.

    Returns overall totals, a per-day series (every bucket is pre-seeded with zeros, so
    missing days are filled in and charts render as a continuous line) and a per-kind
    breakdown over the same window.

    Exposes org-wide token spend and USD cost, so API-key callers must hold the ``manage``
    scope; human sessions carry an implicit wildcard and see it as part of the dashboard.
    """
    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=days - 1)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)

    tokens_expr = func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0)
    cost_expr = func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)

    buckets: dict[date, dict[str, float]] = {
        start_date + timedelta(days=i): {"requests": 0, "tokens": 0, "cost": 0.0}
        for i in range(days)
    }

    day_col = func.date(UsageRecord.created_at)
    day_rows = (
        await db.execute(
            select(
                day_col.label("day"),
                func.count().label("requests"),
                tokens_expr.label("tokens"),
                cost_expr.label("cost"),
            )
            .where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.created_at >= start_dt,
            )
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    for row in day_rows:
        bucket = buckets.get(_as_date(row.day))
        if bucket is None:  # pragma: no cover - guards against tz boundary rows
            continue
        bucket["requests"] = int(row.requests or 0)
        bucket["tokens"] = int(row.tokens or 0)
        bucket["cost"] = float(row.cost or 0.0)

    by_day = [
        UsagePoint(
            date=day.isoformat(),
            requests=int(values["requests"]),
            tokens=int(values["tokens"]),
            cost_usd=round(values["cost"], 6),
        )
        for day, values in sorted(buckets.items())
    ]

    total_requests = sum(point.requests for point in by_day)
    total_tokens = sum(point.tokens for point in by_day)
    total_cost = round(sum(point.cost_usd for point in by_day), 6)

    kind_rows = (
        await db.execute(
            select(
                UsageRecord.kind,
                func.count().label("requests"),
                tokens_expr.label("tokens"),
                cost_expr.label("cost"),
            )
            .where(
                UsageRecord.org_id == ctx.org_id,
                UsageRecord.created_at >= start_dt,
            )
            .group_by(UsageRecord.kind)
            .order_by(func.count().desc())
        )
    ).all()

    by_kind = [
        UsageKindBreakdown(
            kind=row.kind,
            requests=int(row.requests or 0),
            tokens=int(row.tokens or 0),
            cost_usd=round(float(row.cost or 0.0), 6),
        )
        for row in kind_rows
    ]

    return UsageSummary(
        total_requests=total_requests,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        by_day=by_day,
        by_kind=by_kind,
    )


@router.get("/audit", response_model=Page[AuditLogEntry])
async def get_audit_log(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> Page[AuditLogEntry]:
    """Paginated, newest-first audit log for the org (admins/owners only).

    The actor's email is resolved via a left join so entries for deleted users
    (or API-key-only actors) still render.
    """
    params = PaginationParams(page=page, page_size=page_size)

    total = int(
        await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.org_id == ctx.org_id)
        )
        or 0
    )

    rows = (
        await db.execute(
            select(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(AuditLog.org_id == ctx.org_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
    ).all()

    items = [
        AuditLogEntry(
            id=log.id,
            action=log.action,
            actor_user_id=log.actor_user_id,
            actor_email=email,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            ip_address=log.ip_address,
            created_at=log.created_at,
        )
        for log, email in rows
    ]

    return Page.create(items, total, params)
