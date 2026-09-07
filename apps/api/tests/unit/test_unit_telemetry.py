"""Tests for ``app.core.telemetry`` and the LLM client's span/latency instrumentation.

``trace.set_tracer_provider`` is once-per-process global state, so these tests never
install a global provider: the disabled path relies on the test environment having no
OTLP endpoint configured, and the recording path routes the module-level tracer under
test to a private ``TracerProvider`` + ``InMemorySpanExporter``.

The LLM calls run against the deterministic offline provider (conftest pins
``EMBEDDING_PROVIDER = "fake"`` and clears the API key), so nothing touches the network.
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import telemetry
from app.core.config import settings
from app.services.llm import ChatMessage, complete, embed_texts, stream_complete
from app.services.llm import client as llm_client
from app.services.llm.client import CompletionResult, EmbeddingResult


# --------------------------------------------------------------------------- #
# Disabled path - no OTLP endpoint means no provider, no spans, no network
# --------------------------------------------------------------------------- #
class TestTelemetryDisabled:
    def test_get_tracer_spans_are_non_recording(self) -> None:
        tracer = telemetry.get_tracer("tests.telemetry")
        with tracer.start_as_current_span("noop") as span:
            assert not span.is_recording()

    def test_setup_telemetry_installs_nothing_without_endpoint(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", None)

        def _must_not_run(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("tracing is disabled; this must not be called")

        monkeypatch.setattr(telemetry, "OTLPSpanExporter", _must_not_run)
        monkeypatch.setattr(telemetry.trace, "set_tracer_provider", _must_not_run)

        telemetry.setup_telemetry("tests")
        assert telemetry._provider is None

    def test_shutdown_telemetry_is_a_no_op_without_provider(self) -> None:
        assert telemetry._provider is None
        telemetry.shutdown_telemetry()
        assert telemetry._provider is None


# --------------------------------------------------------------------------- #
# Helper parsing / result dataclass contract
# --------------------------------------------------------------------------- #
class TestOtlpHelpers:
    def test_sample_ratio_clamps_out_of_range_without_raising(self, monkeypatch) -> None:
        # TraceIdRatioBased raises for values outside [0, 1]; a misconfigured env var
        # must be coerced to a safe ratio (and logged) rather than crash startup.
        for raw, expected in [(10, 1.0), (-0.5, 0.0), (0.25, 0.25), ("nope", 1.0)]:
            monkeypatch.setattr(settings, "OTEL_TRACES_SAMPLE_RATIO", raw)
            assert telemetry._sample_ratio() == expected

    def test_traces_endpoint_appends_path_once(self) -> None:
        assert telemetry._traces_endpoint("http://otel:4318") == "http://otel:4318/v1/traces"
        assert (
            telemetry._traces_endpoint("http://otel:4318/v1/traces") == "http://otel:4318/v1/traces"
        )


class TestResultLatencyFields:
    def test_latency_ms_defaults_to_zero(self) -> None:
        assert EmbeddingResult(vectors=[], model="m").latency_ms == 0
        assert CompletionResult(text="t", model="m").latency_ms == 0


# --------------------------------------------------------------------------- #
# Recording path - LLM client spans captured by a private in-memory provider
# --------------------------------------------------------------------------- #
@pytest.fixture
def llm_spans(monkeypatch):
    """Route the LLM client's module-level tracer to a local in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm_client, "tracer", provider.get_tracer("tests.llm"))
    yield exporter
    provider.shutdown()


class TestLlmClientInstrumentation:
    async def test_complete_offline_records_chat_span_with_gen_ai_attributes(
        self, llm_spans
    ) -> None:
        marker = "MARKER-DO-NOT-TRACE-9f3a"
        result = await complete([ChatMessage(role="user", content=f"hi {marker}")])
        assert result.provider == "offline"
        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

        chat_spans = [s for s in llm_spans.get_finished_spans() if s.name.startswith("chat ")]
        assert len(chat_spans) == 1
        attrs = chat_spans[0].attributes
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.request.model"] == settings.DEFAULT_COMPLETION_MODEL
        assert attrs["gen_ai.system"] == "offline"
        assert attrs["gen_ai.usage.input_tokens"] >= 1
        assert attrs["gen_ai.usage.output_tokens"] >= 1
        assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
        # Metadata only: message content must never leak into span attributes.
        assert all(marker not in str(v) for v in attrs.values())

    async def test_embed_texts_offline_records_embeddings_span_and_latency(self, llm_spans) -> None:
        result = await embed_texts(["alpha", "beta"])
        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

        spans = [s for s in llm_spans.get_finished_spans() if s.name.startswith("embeddings ")]
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs["gen_ai.operation.name"] == "embeddings"
        assert attrs["gen_ai.system"] == "offline"
        assert attrs["app.text_count"] == 2
        assert attrs["gen_ai.usage.input_tokens"] == result.tokens
        # Metadata only: the embedded text must never leak into span attributes.
        assert all("alpha" not in str(v) for v in attrs.values())

    async def test_stream_complete_populates_latency_and_ttft_meta(self, llm_spans) -> None:
        meta_out: dict[str, Any] = {}
        text = "".join(
            [
                delta
                async for delta in stream_complete(
                    [ChatMessage(role="user", content="q")], meta_out=meta_out
                )
            ]
        )
        assert text
        assert meta_out["provider"] == "offline"
        assert meta_out["model"] == "offline"
        assert isinstance(meta_out["latency_ms"], int)
        assert isinstance(meta_out["ttft_ms"], int)
        assert 0 <= meta_out["ttft_ms"] <= meta_out["latency_ms"]

        span = next(s for s in llm_spans.get_finished_spans() if s.name.startswith("chat "))
        assert span.attributes["app.latency_ms"] == meta_out["latency_ms"]
        assert span.attributes["app.ttft_ms"] == meta_out["ttft_ms"]


class TestNoiseFilteringSpanProcessor:
    """Parentless Redis client spans (arq queue polling) are dropped before export."""

    @pytest.fixture
    def filtered_provider(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        processor = telemetry._NoiseFilteringSpanProcessor(exporter)
        provider.add_span_processor(processor)
        yield provider, exporter
        processor.force_flush()
        provider.shutdown()

    def _exported_names(self, provider, exporter) -> set[str]:
        provider.force_flush()
        return {span.name for span in exporter.get_finished_spans()}

    def test_root_redis_span_is_dropped(self, filtered_provider) -> None:
        provider, exporter = filtered_provider
        redis_tracer = provider.get_tracer("opentelemetry.instrumentation.redis")
        with redis_tracer.start_as_current_span("ZRANGEBYSCORE"):
            pass
        assert "ZRANGEBYSCORE" not in self._exported_names(provider, exporter)

    def test_parented_redis_span_is_kept(self, filtered_provider) -> None:
        provider, exporter = filtered_provider
        app_tracer = provider.get_tracer("app.services.retrieval")
        redis_tracer = provider.get_tracer("opentelemetry.instrumentation.redis")
        with app_tracer.start_as_current_span("retrieval.embed_query"):
            with redis_tracer.start_as_current_span("GET"):
                pass
        names = self._exported_names(provider, exporter)
        assert {"retrieval.embed_query", "GET"} <= names

    def test_root_non_redis_span_is_kept(self, filtered_provider) -> None:
        provider, exporter = filtered_provider
        worker_tracer = provider.get_tracer("app.workers.tasks")
        with worker_tracer.start_as_current_span("worker.ingest_document"):
            pass
        assert "worker.ingest_document" in self._exported_names(provider, exporter)
