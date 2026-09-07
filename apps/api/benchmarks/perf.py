"""Single-request performance characterization for the benchmark harness.

:func:`measure_stage_latency` runs the query set once per retrieval config and reports
the median duration of each pipeline stage, reusing the OpenTelemetry spans the real
services already emit (``retrieval.scope``, ``retrieval.embed_query``,
``retrieval.vector_search``, ``retrieval.keyword_search``, ``retrieval.retrieve`` and
``rag.answer``). No timing is added to the services.

Spans are captured non-destructively: a local :class:`TracerProvider` backed by an
:class:`InMemorySpanExporter` is installed, and the module-level tracers of
``app.services.retrieval`` / ``app.services.rag`` are temporarily rerouted to it, then
restored. The global tracer provider is never touched, so the module is import-safe and
offline-safe. When span capture yields nothing (e.g. tracing behaves unexpectedly) the
end-to-end ``total`` falls back to the harness's own ``QueryRun.total_latency_ms`` and
the per-stage keys are simply absent.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import get_logger

if TYPE_CHECKING:
    from benchmarks.dataset import BenchmarkDataset
    from benchmarks.harness import BenchmarkRun, RetrievalConfig

logger = get_logger(__name__)

# The retrieval/RAG spans the app emits, in pipeline order. Which subset appears depends
# on the config: vector-only retrieval emits no ``retrieval.keyword_search`` span.
_STAGE_NAMES = (
    "retrieval.scope",
    "retrieval.embed_query",
    "retrieval.vector_search",
    "retrieval.keyword_search",
    "retrieval.retrieve",
    "rag.answer",
)


@dataclass
class StageLatency:
    """Median per-stage latency (milliseconds) for one retrieval config, plus ``total``."""

    config_key: str
    per_stage_ms: dict[str, float] = field(default_factory=dict)


def _median_stage_durations(spans) -> dict[str, float]:
    """Median duration (ms) of each captured stage span, keyed by span name."""
    by_name: dict[str, list[float]] = {}
    for span in spans:
        if span.name in _STAGE_NAMES and span.start_time and span.end_time:
            duration_ms = (span.end_time - span.start_time) / 1_000_000
            by_name.setdefault(span.name, []).append(duration_ms)
    return {name: statistics.median(values) for name, values in by_name.items()}


def _total_ms(per_stage: dict[str, float], run: BenchmarkRun | None) -> float:
    """End-to-end median latency: the outermost captured span, or the harness's own
    ``total_latency_ms`` when no stage spans were captured."""
    if "rag.answer" in per_stage:
        return per_stage["rag.answer"]
    if "retrieval.retrieve" in per_stage:
        return per_stage["retrieval.retrieve"]
    latencies = [qr.total_latency_ms for qr in getattr(run, "runs", []) if qr.total_latency_ms]
    return statistics.median(latencies) if latencies else 0.0


def _reset_db_pool() -> None:
    """Drop connections pooled on a previous event loop before we open a new one.

    The app's async engine pools asyncpg connections bound to the loop that first used
    them. ``measure_stage_latency`` runs after the main harness pass (a different, now
    closed ``asyncio.run`` loop), so those pooled connections are unusable here. Disposing
    with ``close=False`` swaps in a fresh pool without touching the dead-loop connections;
    the new loop then creates its own. Best-effort: a missing engine must not break perf.
    """
    try:
        from app.core.db import engine

        engine.sync_engine.dispose(close=False)
    except Exception as exc:  # noqa: BLE001 - perf must degrade, never crash the run
        logger.warning("perf_pool_reset_failed", error=str(exc))


async def _measure_all(
    ds: BenchmarkDataset, configs: list[RetrievalConfig], exporter: InMemorySpanExporter
) -> list[StageLatency]:
    """Run every config within a SINGLE event loop so the DB pool rebinds once and stays
    valid across configs, capturing each config's spans in isolation."""
    from benchmarks import harness

    results: list[StageLatency] = []
    for cfg in configs:
        exporter.clear()
        try:
            run = await harness.run(ds, [cfg], with_answers=True)
        except Exception as exc:  # noqa: BLE001 - one config failing must not abort all
            logger.warning("perf_config_failed", config=cfg.key, error=str(exc))
            results.append(StageLatency(cfg.key, {}))
            continue

        per_stage = _median_stage_durations(exporter.get_finished_spans())
        per_stage["total"] = _total_ms(per_stage, run)
        logger.info(
            "perf_config",
            config=cfg.key,
            stages=len(per_stage) - 1,
            total_ms=round(per_stage["total"], 3),
        )
        results.append(StageLatency(cfg.key, per_stage))
    return results


def measure_stage_latency(
    ds: BenchmarkDataset, configs: list[RetrievalConfig]
) -> list[StageLatency]:
    """Measure median per-stage latency for each config by driving the real pipeline.

    Each config is run once (via the harness, with answers on so the ``rag.answer`` span
    is emitted) with the span exporter cleared beforehand, so the finished spans belong to
    that config alone. Self-contained: all configs run in one event loop after the shared
    DB pool is reset, so a caller may invoke this with any config list regardless of
    earlier ``asyncio.run`` boundaries. A config whose run fails (e.g. infrastructure
    unreachable) is reported with an empty ``per_stage_ms`` rather than aborting.
    """
    try:
        from benchmarks import harness  # noqa: F401 - fail fast (and gracefully) if absent
    except Exception as exc:  # noqa: BLE001 - degrade gracefully if the harness is absent
        logger.warning("perf_harness_unavailable", error=str(exc))
        return [StageLatency(cfg.key, {}) for cfg in configs]

    import app.services.rag as rag_mod
    import app.services.retrieval as retrieval_mod

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    original_retrieval_tracer = retrieval_mod.tracer
    original_rag_tracer = rag_mod.tracer
    retrieval_mod.tracer = provider.get_tracer("app.services.retrieval")
    rag_mod.tracer = provider.get_tracer("app.services.rag")

    try:
        _reset_db_pool()
        return asyncio.run(_measure_all(ds, configs, exporter))
    finally:
        retrieval_mod.tracer = original_retrieval_tracer
        rag_mod.tracer = original_rag_tracer
        provider.shutdown()
