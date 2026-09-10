"""arq ``WorkerSettings`` - the entrypoint for the ingestion worker process.

Run with::

    arq app.workers.settings.WorkerSettings

This module is only imported inside the worker process, so importing ``arq`` at
module top-level here is fine (the API never imports this module).
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.telemetry import setup_telemetry, shutdown_telemetry
from app.workers.tasks import (
    expire_device_authorizations_task,
    flag_stale_content_task,
    ingest_document_task,
    reap_stuck_documents_task,
    sync_data_source_task,
    sync_due_data_sources_task,
)


async def startup(ctx: dict[str, Any]) -> None:
    """Initialize logging and tracing for the worker process.

    Importing the tasks module already configures logging via ``get_logger``; calling it
    again here re-routes arq's own loggers - whose handlers are installed after that first
    call - through the shared root handler and adds the telemetry provider.
    """
    configure_logging()
    setup_telemetry("third-brain-worker")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Flush pending spans before the worker process exits."""
    shutdown_telemetry()


class WorkerSettings:
    """Configuration consumed by the ``arq`` CLI.

    The concurrency/timeout knobs (``max_jobs``, ``job_timeout``, ``keep_result``) are
    sized for the fact that ingestion can be slow (large PDFs, remote fetches, embedding
    round-trips).
    """

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    functions = [ingest_document_task, sync_data_source_task]
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [
        cron(reap_stuck_documents_task, minute={0, 15, 30, 45}, run_at_startup=True),
        cron(sync_due_data_sources_task, minute=set(range(0, 60, 5))),
        cron(flag_stale_content_task, minute={7}),
        cron(expire_device_authorizations_task, minute=set(range(0, 60, 5))),
    ]
    """Periodic sweeps, in declaration order.

    Documents stranded mid-ingestion (crash/timeout with no redelivery) are swept every
    15 minutes so they surface as FAILED and can be reprocessed; due data-source syncs
    run every 5 minutes (each source honours its own ``sync_interval_minutes``);
    verified-but-past-review documents/answers are flipped to STALE once an hour; and
    CLI device-auth flows abandoned after approval are closed out every 5 minutes, so no
    orphaned API key or encrypted key plaintext lingers past the flow's expiry.
    """

    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
