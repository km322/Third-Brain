"""arq task functions.

Each task opens its own ``AsyncSession`` (the worker has no request-scoped session)
and delegates to the ingestion service, which owns its transaction. Task functions
must never raise for expected failures - the ingestion service records a ``failed``
document state itself - so a poisoned job doesn't wedge the queue.

Correlation: ``enqueue_ingest`` ships ``request_id`` and W3C trace context
(``traceparent``/``tracestate``) as job kwargs. Tasks bind job-scoped fields into
structlog contextvars and run under a CONSUMER span parented to the enqueuing
request's trace, so worker log lines and spans correlate with the API side. All
correlation kwargs default to ``None`` so jobs enqueued with the old signature
still execute.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from opentelemetry import propagate
from opentelemetry.trace import SpanKind
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.telemetry import get_tracer
from app.models.api_key import ApiKey
from app.models.datasource import DataSource
from app.models.device_auth import DeviceAuthorization
from app.models.enums import DataSourceStatus, DeviceAuthStatus
from app.services.datasource_sync import reap_stuck_data_sources, sync_data_source
from app.services.ingestion import ingest_document, reap_stuck_documents
from app.services.verification import flag_stale

logger = get_logger(__name__)
tracer = get_tracer(__name__)


def _extract_trace_context(traceparent: str | None, tracestate: str | None):
    """Rebuild the enqueuing request's trace context, or ``None`` when absent."""
    if not traceparent:
        return None
    carrier = {"traceparent": traceparent}
    if tracestate:
        carrier["tracestate"] = tracestate
    return propagate.extract(carrier)


async def ingest_document_task(
    ctx: dict[str, Any],
    doc_id: str,
    request_id: str | None = None,
    traceparent: str | None = None,
    tracestate: str | None = None,
) -> None:
    """Worker entrypoint: ingest the document identified by ``doc_id``."""
    structlog.contextvars.clear_contextvars()
    bound: dict[str, Any] = {
        "job_id": ctx.get("job_id"),
        "job_try": ctx.get("job_try"),
        "document_id": doc_id,
    }
    if request_id:
        bound["request_id"] = request_id
    structlog.contextvars.bind_contextvars(**bound)

    started = time.monotonic()
    logger.info("worker_ingest_started")
    try:
        with tracer.start_as_current_span(
            "worker.ingest_document",
            context=_extract_trace_context(traceparent, tracestate),
            kind=SpanKind.CONSUMER,
        ):
            async with SessionLocal() as db:
                status = await ingest_document(db, uuid.UUID(str(doc_id)))
        logger.info(
            "worker_ingest_finished",
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        structlog.contextvars.clear_contextvars()


async def reap_stuck_documents_task(ctx: dict[str, Any]) -> int:
    """Periodic (cron) sweep that fails work stranded mid-flight by a crash/timeout.

    Reaps both documents stuck in PROCESSING and data sources stuck in SYNCING (each of which
    the due-sync cron would otherwise never reschedule). Returns the number of documents reaped.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
    try:
        async with SessionLocal() as db:
            reaped = await reap_stuck_documents(db)
            await reap_stuck_data_sources(db)
            return reaped
    finally:
        structlog.contextvars.clear_contextvars()


async def sync_data_source_task(
    ctx: dict[str, Any], source_id: str, request_id: str | None = None
) -> None:
    """Worker entrypoint: sync a single data source. Never raises (self-reports ERROR)."""
    structlog.contextvars.clear_contextvars()
    bound: dict[str, Any] = {"job_id": ctx.get("job_id"), "data_source_id": source_id}
    if request_id:
        bound["request_id"] = request_id
    structlog.contextvars.bind_contextvars(**bound)
    try:
        async with SessionLocal() as db:
            await sync_data_source(db, uuid.UUID(str(source_id)))
    except Exception:  # pragma: no cover - sync marks ERROR + logs; keep the queue healthy
        logger.exception("worker_sync_failed", data_source_id=source_id)
    finally:
        structlog.contextvars.clear_contextvars()


async def expire_device_authorizations_task(ctx: dict[str, Any]) -> int:
    """Periodic (cron) sweep that closes out expired CLI device-auth flows.

    Lazy expiry (resolved on every poll/read) is the fast path; this is the backstop for
    a flow that was approved but then abandoned before the CLI redeemed it. Such a row
    would otherwise keep a live ApiKey and the key's encrypted plaintext at rest forever.

    Revokes keys of still-APPROVED-and-expired rows FIRST, then flips every still
    PENDING/APPROVED expired row to EXPIRED and drops the ciphertext. Both statements are
    guarded by the status predicate so a poll redeeming a row at the same instant (the CAS
    that flips APPROVED -> CONSUMED) is not clobbered. Returns the number of rows expired.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
    try:
        async with SessionLocal() as db:
            now = datetime.now(UTC)
            orphan_keys = (
                select(DeviceAuthorization.api_key_id)
                .where(
                    DeviceAuthorization.status == DeviceAuthStatus.APPROVED,
                    DeviceAuthorization.expires_at < now,
                    DeviceAuthorization.api_key_id.is_not(None),
                )
                .scalar_subquery()
            )
            await db.execute(update(ApiKey).where(ApiKey.id.in_(orphan_keys)).values(revoked=True))
            expired = await db.execute(
                update(DeviceAuthorization)
                .where(
                    DeviceAuthorization.status.in_(
                        [DeviceAuthStatus.PENDING, DeviceAuthStatus.APPROVED]
                    ),
                    DeviceAuthorization.expires_at < now,
                )
                .values(status=DeviceAuthStatus.EXPIRED, encrypted_secret=None)
            )
            await db.commit()
            return expired.rowcount or 0
    finally:
        structlog.contextvars.clear_contextvars()


async def flag_stale_content_task(ctx: dict[str, Any]) -> dict[str, int]:
    """Periodic (cron) sweep flipping verified-but-past-review content to STALE."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
    try:
        async with SessionLocal() as db:
            return await flag_stale(db)
    finally:
        structlog.contextvars.clear_contextvars()


async def sync_due_data_sources_task(ctx: dict[str, Any]) -> int:
    """Periodic (cron) sweep that syncs ACTIVE sources whose schedule has elapsed."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
    synced = 0
    try:
        async with SessionLocal() as db:
            now = datetime.now(UTC)
            rows = (
                await db.execute(
                    select(
                        DataSource.id,
                        DataSource.last_synced_at,
                        DataSource.sync_interval_minutes,
                    ).where(
                        DataSource.status == DataSourceStatus.ACTIVE,
                        DataSource.sync_interval_minutes.is_not(None),
                    )
                )
            ).all()
            due = [
                sid
                for sid, last, interval in rows
                if last is None or last < now - timedelta(minutes=interval)
            ]
            for sid in due:
                try:
                    await sync_data_source(db, sid)
                    synced += 1
                except Exception:  # pragma: no cover - one bad source must not stop the sweep
                    logger.exception("scheduled_sync_failed", data_source_id=str(sid))
        return synced
    finally:
        structlog.contextvars.clear_contextvars()
