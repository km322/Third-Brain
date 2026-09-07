"""Enqueue side of the ingestion queue, used by the documents API.

``enqueue_ingest`` tries to push a job onto Redis for the arq worker to pick up. If
Redis or arq is unavailable (local dev without a worker, misconfigured broker, …) it
transparently falls back to running the ingestion inline as a background task, so that
uploads still get indexed even with no worker running. The fallback opens its own
``AsyncSession`` because the request's session is closed by the time it runs.

``arq`` is imported lazily so this module (imported by the API) works without it.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from opentelemetry import propagate

from app.core.config import settings
from app.core.logging import get_logger
from app.core.middleware import current_request_id

logger = get_logger(__name__)

INGEST_TASK_NAME = "ingest_document_task"

_pool = None
_pool_lock = asyncio.Lock()

# Strong references to detached inline-ingestion tasks. The event loop only keeps a weak
# reference to a bare ``create_task`` result, so without this an un-awaited task can be
# garbage-collected mid-run - stranding the document in ``pending``.
_bg_tasks: set[asyncio.Task] = set()


async def get_arq_pool():
    """Return a lazily-created, process-wide arq Redis pool."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                from arq import create_pool
                from arq.connections import RedisSettings

                _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _pool


async def _run_inline(doc_id: uuid.UUID) -> None:
    """Fallback: run the ingestion pipeline in-process with a fresh session."""
    from app.core.db import SessionLocal
    from app.services.ingestion import ingest_document

    started = time.monotonic()
    logger.info("inline_ingestion_started", document_id=str(doc_id), inline=True)
    try:
        async with SessionLocal() as db:
            await ingest_document(db, doc_id)
        logger.info(
            "inline_ingestion_finished",
            document_id=str(doc_id),
            inline=True,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:  # pragma: no cover - defensive; ingestion self-reports failures
        logger.exception("inline_ingestion_failed", document_id=str(doc_id), inline=True)


def _correlation_kwargs() -> dict[str, str | None]:
    """Correlation fields carried across the queue boundary to the worker.

    ``request_id`` ties worker log lines back to the originating API request;
    ``traceparent``/``tracestate`` (W3C trace context) parent the worker span to the
    request's trace.

    The worker task defaults these kwargs to ``None``, so a job enqueued by an OLD API
    (without them) still runs on a NEW worker. The reverse does NOT hold: a NEW API
    enqueuing these kwargs breaks an OLD worker whose ``ingest_document_task(ctx, doc_id)``
    rejects them (TypeError, the job fails and the document is stuck until the reaper).
    During a rolling deploy the worker must therefore be updated before (or together
    with) the API.
    """
    request_id = current_request_id()
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return {
        "request_id": request_id if request_id != "-" else None,
        "traceparent": carrier.get("traceparent"),
        "tracestate": carrier.get("tracestate"),
    }


async def enqueue_ingest(doc_id: uuid.UUID) -> bool:
    """Schedule ingestion of ``doc_id``.

    Returns ``True`` if the job was enqueued to the worker, ``False`` if it fell back
    to inline execution. Either way the document will be processed.
    """
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job(INGEST_TASK_NAME, str(doc_id), **_correlation_kwargs())
        logger.info("ingest_enqueued", document_id=str(doc_id))
        return True
    except Exception as exc:
        logger.warning(
            "ingest_enqueue_failed", document_id=str(doc_id), error=str(exc), inline=True
        )
        # Detach from the request lifecycle so the response returns immediately, but keep a
        # strong reference until it finishes (see ``_bg_tasks``). Request-scoped contextvars
        # (request_id binding, trace context) flow into the task automatically.
        task = asyncio.create_task(_run_inline(doc_id))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return False
