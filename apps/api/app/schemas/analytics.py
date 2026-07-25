"""Response schemas for the analytics module.

These mirror the shapes consumed by the dashboard's Overview, Usage and Audit
Log pages (see ``apps/web/lib/types.ts``): ``AnalyticsOverview``,
``UsageSummary`` (with a gap-free ``by_day`` series + ``by_kind`` breakdown) and
paginated ``AuditLogEntry`` rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import UsageKind


class AnalyticsOverview(BaseModel):
    """Headline KPIs for the current organization."""

    documents: int = Field(description="Total documents in the org.")
    collections: int = Field(description="Total collections (knowledge bases) in the org.")
    members: int = Field(description="Total org memberships.")
    api_keys: int = Field(description="Active (non-revoked) API keys.")
    searches_7d: int = Field(description="Search operations in the last 7 days.")
    tokens_30d: int = Field(description="Tokens consumed (in + out) in the last 30 days.")
    cost_30d: float = Field(description="Estimated spend in USD over the last 30 days.")


class UsagePoint(BaseModel):
    """A single day in the usage time-series."""

    date: str = Field(description="ISO date (YYYY-MM-DD, UTC).")
    requests: int
    tokens: int
    cost_usd: float


class UsageKindBreakdown(BaseModel):
    """Aggregated usage for one :class:`UsageKind`."""

    kind: UsageKind
    requests: int
    tokens: int
    cost_usd: float


class UsageSummary(BaseModel):
    """Totals plus a continuous per-day series and a per-kind breakdown."""

    total_requests: int
    total_tokens: int
    total_cost_usd: float
    by_day: list[UsagePoint]
    by_kind: list[UsageKindBreakdown]


class AuditLogEntry(BaseModel):
    """A single audit-log row with the actor's email resolved when available."""

    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None = None
    actor_email: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    ip_address: str | None = None
    created_at: datetime
