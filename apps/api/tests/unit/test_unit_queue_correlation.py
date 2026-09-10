"""Tests for request -> worker correlation on the ingestion queue.

``enqueue_ingest`` must ship the enqueuing request's ``request_id`` and W3C trace
context (``traceparent``/``tracestate``) as job kwargs so worker log lines and spans
correlate with the originating API request. The arq pool is faked (no Redis) and the
request id is bound through the same ``ContextVar`` the middleware uses.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider

from app.core.middleware import request_id_ctx
from app.workers import queue as queue_mod


class FakePool:
    """Records ``enqueue_job`` calls instead of talking to Redis."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def enqueue_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


@pytest.fixture
def fake_pool(monkeypatch) -> FakePool:
    pool = FakePool()

    async def _get_pool() -> FakePool:
        return pool

    monkeypatch.setattr(queue_mod, "get_arq_pool", _get_pool)
    return pool


class TestEnqueueCorrelation:
    async def test_job_carries_doc_id_request_id_and_trace_kwargs(self, fake_pool) -> None:
        """Tracing is disabled in the unit tier, so the trace-context kwargs are present per the
        job signature but carry no value."""
        doc_id = uuid.uuid4()
        token = request_id_ctx.set("req-corr-123")
        try:
            enqueued = await queue_mod.enqueue_ingest(doc_id)
        finally:
            request_id_ctx.reset(token)

        assert enqueued is True
        assert len(fake_pool.calls) == 1
        args, kwargs = fake_pool.calls[0]
        assert args == (queue_mod.INGEST_TASK_NAME, str(doc_id))
        assert kwargs["request_id"] == "req-corr-123"
        assert "traceparent" in kwargs
        assert kwargs["traceparent"] is None
        assert "tracestate" in kwargs

    async def test_no_bound_request_id_becomes_none(self, fake_pool) -> None:
        assert request_id_ctx.get() == "-"
        assert await queue_mod.enqueue_ingest(uuid.uuid4()) is True
        _, kwargs = fake_pool.calls[0]
        assert kwargs["request_id"] is None

    async def test_traceparent_carries_the_active_span_trace_id(self, fake_pool) -> None:
        provider = TracerProvider()
        tracer = provider.get_tracer("tests.queue")
        doc_id = uuid.uuid4()
        with tracer.start_as_current_span("request") as span:
            assert await queue_mod.enqueue_ingest(doc_id) is True
            trace_id = format(span.get_span_context().trace_id, "032x")
        provider.shutdown()

        _, kwargs = fake_pool.calls[0]
        assert kwargs["traceparent"] is not None
        assert trace_id in kwargs["traceparent"]

    async def test_broker_failure_falls_back_to_inline_ingestion(self, monkeypatch) -> None:
        async def _broken_pool():
            raise ConnectionError("redis down")

        monkeypatch.setattr(queue_mod, "get_arq_pool", _broken_pool)

        ran = asyncio.Event()
        seen: list[uuid.UUID] = []

        async def _fake_inline(doc_id: uuid.UUID) -> None:
            seen.append(doc_id)
            ran.set()

        monkeypatch.setattr(queue_mod, "_run_inline", _fake_inline)

        doc_id = uuid.uuid4()
        assert await queue_mod.enqueue_ingest(doc_id) is False
        await asyncio.wait_for(ran.wait(), timeout=2)
        assert seen == [doc_id]
