"""Usage metering + audit logging helpers shared by every route.

Both helpers ``add`` the record inside a SAVEPOINT (``begin_nested``) and NOT commit - the
calling request commits as part of its normal transaction. The savepoint is what makes
"metering must never break a user-facing request" actually true: a failed metering flush
(e.g. an ``api_key_id`` FK that was deleted mid-request) rolls back only the metering row,
leaving the caller's own transaction committable. We flush any pending caller state into
the outer transaction *before* opening the savepoint so a metering rollback can never
discard the caller's real work.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.enums import UsageKind
from app.models.usage import UsageRecord

logger = get_logger(__name__)


async def record_usage(
    db: AsyncSession,
    ctx: AuthContext,
    kind: UsageKind,
    *,
    provider: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    units: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    meta: dict[str, Any] | None = None,
) -> None:
    try:
        await db.flush()
        async with db.begin_nested():
            db.add(
                UsageRecord(
                    org_id=ctx.org_id,
                    user_id=ctx.user_id,
                    api_key_id=ctx.api_key.id if ctx.api_key else None,
                    kind=kind,
                    provider=provider,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    units=units,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    meta=meta or {},
                )
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to record usage: %s", exc)


async def record_audit(
    db: AsyncSession,
    ctx: AuthContext | None,
    action: str,
    *,
    org_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    try:
        resolved_org = org_id or (ctx.org_id if ctx else None)
        if resolved_org is None:
            return
        await db.flush()
        async with db.begin_nested():
            db.add(
                AuditLog(
                    org_id=resolved_org,
                    actor_user_id=ctx.user_id if ctx else None,
                    actor_api_key_id=(ctx.api_key.id if ctx and ctx.api_key else None),
                    action=action,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id is not None else None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    meta=meta or {},
                )
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to record audit log: %s", exc)
