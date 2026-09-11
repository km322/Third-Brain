"""OpenTelemetry tracing setup.

Tracing is opt-in: when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset nothing is installed,
``get_tracer`` hands out non-recording no-op tracers and no network calls are ever
attempted, so dev/CI run clean with zero configuration.

Process entrypoints (API in ``app.main``, arq worker) call :func:`setup_telemetry` once
at startup and :func:`shutdown_telemetry` on exit; everything else just uses
``get_tracer(__name__)``.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.util.re import parse_env_headers

from app import __version__
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_provider: TracerProvider | None = None


class _NoiseFilteringSpanProcessor(BatchSpanProcessor):
    """Drop parentless Redis client spans before export.

    The arq worker polls Redis several times a second; each poll would otherwise
    become a root span (hundreds of thousands per day of pure noise). Redis calls
    made while serving a request or running a job have a parent span and are kept.
    """

    def on_end(self, span) -> None:
        scope_name = span.instrumentation_scope.name if span.instrumentation_scope else ""
        if span.parent is None and "redis" in scope_name:
            return
        super().on_end(span)


def _sample_ratio() -> float:
    """Return ``OTEL_TRACES_SAMPLE_RATIO`` clamped to the sampler's valid [0.0, 1.0] range.

    ``TraceIdRatioBased`` raises for a ratio outside that range, and ``setup_telemetry``
    runs at process startup, so a misconfigured value (out of range, or not a number) is
    coerced to a safe ratio and logged rather than raised - a typo can never crash the process.
    """
    requested = settings.OTEL_TRACES_SAMPLE_RATIO
    try:
        ratio = float(requested)
    except (TypeError, ValueError):
        logger.warning("otel_sample_ratio_clamped", requested=requested, used=1.0)
        return 1.0
    used = max(0.0, min(1.0, ratio))
    if used != ratio:
        logger.warning("otel_sample_ratio_clamped", requested=requested, used=used)
    return used


def _traces_endpoint(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/v1/traces") else f"{base}/v1/traces"


def setup_telemetry(service_name: str | None = None) -> None:
    """Install the tracer provider and instrument shared libraries (idempotent).

    ``app.core.db`` is imported inside the function rather than at module level to avoid
    an import cycle.
    """
    global _provider
    if _provider is not None:
        return
    if not settings.otel_enabled:
        logger.debug("telemetry_disabled", reason="no_otlp_endpoint")
        return

    resource = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME or service_name or "third-brain-api",
            "service.version": __version__,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )
    sample_ratio = _sample_ratio()
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    raw_headers = settings.OTEL_EXPORTER_OTLP_HEADERS
    otlp_headers = parse_env_headers(raw_headers) if raw_headers else None
    exporter = OTLPSpanExporter(
        endpoint=_traces_endpoint(settings.OTEL_EXPORTER_OTLP_ENDPOINT),
        headers=otlp_headers or None,
    )
    provider.add_span_processor(_NoiseFilteringSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider

    from app.core.db import engine

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()

    logger.info(
        "telemetry_enabled",
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        sample_ratio=sample_ratio,
    )


def instrument_app(app: FastAPI) -> None:
    """Attach FastAPI server-span instrumentation (no-op when tracing is disabled).

    ``exclude_spans``: per-message ASGI receive/send spans add dozens of internal spans to
    every streamed (SSE) response without diagnostic value.
    """
    if settings.otel_enabled:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="/health,/api/v1/health,/docs,/openapi.json",
            exclude_spans=["receive", "send"],
        )


def shutdown_telemetry() -> None:
    """Flush pending spans and shut the provider down (no-op when tracing is disabled)."""
    global _provider
    if _provider is None:
        return
    _provider.force_flush()
    _provider.shutdown()
    _provider = None


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer; always safe - a no-op tracer when tracing is disabled."""
    return trace.get_tracer(name)
