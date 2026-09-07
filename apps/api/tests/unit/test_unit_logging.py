"""Pure-logic tests for the structured logging pipeline (``app.core.logging``).

The pipeline routes every log line - structlog call sites and stdlib loggers alike -
through one shared processor chain. These tests exercise that chain directly
(redaction, trace correlation) and end-to-end through a private ``StringIO`` handler
whose formatter forces the JSON renderer, so nothing depends on global stdout state
or the process-wide console/json choice.
"""

from __future__ import annotations

import io
import json
import logging
import uuid

import pytest
import structlog

from app.core import logging as app_logging
from app.core.config import settings
from app.core.logging import (
    add_trace_context,
    configure_logging,
    get_logger,
    redact_sensitive_values,
)


# --------------------------------------------------------------------------- #
# redact_sensitive_values - credential-shaped keys are censored
# --------------------------------------------------------------------------- #
class TestRedactSensitiveValues:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "user_password",
            "PASSWORD",
            "secret",
            "client_secret",
            "token",
            "access_token",
            "api_key",
            "apikey",
            "OPENAI_API_KEY",
            "authorization",
            "Authorization",
            "cookie",
            "session_cookie",
            "credential",
            "aws_credentials",
        ],
    )
    def test_credential_like_keys_are_censored(self, key: str) -> None:
        out = redact_sensitive_values(None, "info", {key: "hunter2", "event": "login"})
        assert out[key] == "[REDACTED]"
        assert out["event"] == "login"

    def test_non_sensitive_keys_are_left_alone(self) -> None:
        event_dict = {"event": "x", "count": 3, "model": "gpt-4o-mini", "duration_ms": 12.5}
        out = redact_sensitive_values(None, "info", dict(event_dict))
        assert out == event_dict

    def test_id_suffixed_keys_are_identifiers_not_secrets(self) -> None:
        out = redact_sensitive_values(None, "info", {"api_key_id": "abc123", "token_id": "t1"})
        assert out["api_key_id"] == "abc123"
        assert out["token_id"] == "t1"

    @pytest.mark.parametrize(
        "key",
        [
            "tokens",
            "tokens_in",
            "tokens_out",
            "max_tokens",
            "embed_tokens",
            # A count field added in the future is exempt without touching an allowlist.
            "prompt_tokens",
            "completion_tokens",
        ],
    )
    def test_token_count_fields_are_usage_metrics_not_secrets(self, key: str) -> None:
        out = redact_sensitive_values(None, "info", {key: 128, "event": "llm_call"})
        assert out[key] == 128

    @pytest.mark.parametrize("key", ["duration_ms", "retry_count", "chunk_count"])
    def test_measurement_fields_are_not_secrets(self, key: str) -> None:
        out = redact_sensitive_values(None, "info", {key: 7, "event": "ingest"})
        assert out[key] == 7


# --------------------------------------------------------------------------- #
# JSON pipeline - real processor chain rendered to JSON on a private StringIO
# --------------------------------------------------------------------------- #
def _last_json_line(stream: io.StringIO) -> dict:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "expected at least one log line"
    return json.loads(lines[-1])


@pytest.fixture
def json_sink():
    """A structlog logger whose output lands as JSON lines in a ``StringIO``.

    Reuses ``configure_logging``'s ``ProcessorFormatter`` builder - forced to the JSON
    renderer - on a fresh, non-propagating stdlib logger, and clears the structlog
    contextvars around the test.
    """
    configure_logging()
    structlog.contextvars.clear_contextvars()

    name = f"tests.logging.{uuid.uuid4().hex[:8]}"
    stream = io.StringIO()
    formatter = app_logging._build_formatter(structlog.processors.JSONRenderer())
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    std_logger = logging.getLogger(name)
    std_logger.addHandler(handler)
    std_logger.setLevel(logging.DEBUG)
    std_logger.propagate = False

    yield get_logger(name), stream, name

    std_logger.removeHandler(handler)
    structlog.contextvars.clear_contextvars()


class TestJsonPipeline:
    def test_line_is_parseable_json_with_core_fields_and_contextvars(self, json_sink) -> None:
        logger, stream, name = json_sink
        structlog.contextvars.bind_contextvars(request_id="req-abc", org_id="org-1")
        logger.info("user_login", user_count=3)

        payload = _last_json_line(stream)
        assert payload["event"] == "user_login"
        assert payload["level"] == "info"
        assert payload["logger"] == name
        assert "timestamp" in payload
        assert payload["request_id"] == "req-abc"
        assert payload["org_id"] == "org-1"
        assert payload["user_count"] == 3

    def test_old_style_positional_formatting_renders_into_event(self, json_sink) -> None:
        logger, stream, _ = json_sink
        logger.info("x %s", "y")
        assert _last_json_line(stream)["event"] == "x y"

    def test_logger_exception_includes_exception_info(self, json_sink) -> None:
        logger, stream, _ = json_sink
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("op_failed")

        payload = _last_json_line(stream)
        assert payload["event"] == "op_failed"
        assert payload["level"] == "error"
        exception_text = payload.get("exception", "")
        assert "ValueError" in exception_text
        assert "boom" in exception_text

    def test_sensitive_fields_are_redacted_end_to_end(self, json_sink) -> None:
        logger, stream, _ = json_sink
        logger.info("connector_saved", api_key="tb_supersecret", connector_id="c1")

        payload = _last_json_line(stream)
        assert payload["api_key"] == "[REDACTED]"
        assert "tb_supersecret" not in stream.getvalue()
        assert payload["connector_id"] == "c1"


# --------------------------------------------------------------------------- #
# Renderer selection - LOG_FORMAT decides json vs console
# --------------------------------------------------------------------------- #
class TestRendererSelection:
    def test_json_mode_selects_json_renderer(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LOG_FORMAT", "json")
        assert isinstance(app_logging._renderer(), structlog.processors.JSONRenderer)

    def test_console_mode_selects_console_renderer(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "LOG_FORMAT", "console")
        assert isinstance(app_logging._renderer(), structlog.dev.ConsoleRenderer)


# --------------------------------------------------------------------------- #
# add_trace_context - log lines correlate with the active span
# --------------------------------------------------------------------------- #
class TestAddTraceContext:
    def test_no_active_span_leaves_event_dict_alone(self) -> None:
        out = add_trace_context(None, "info", {"event": "x"})
        assert "trace_id" not in out
        assert "span_id" not in out

    def test_recording_span_stamps_trace_and_span_ids(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("tests.logging")
        with tracer.start_as_current_span("op") as span:
            out = add_trace_context(None, "info", {"event": "x"})
            ctx = span.get_span_context()
            assert out["trace_id"] == format(ctx.trace_id, "032x")
            assert out["span_id"] == format(ctx.span_id, "016x")
        provider.shutdown()


# --------------------------------------------------------------------------- #
# Third-party logger re-routing - uvicorn/arq records reach the root handler
# --------------------------------------------------------------------------- #
class TestThirdPartyLoggerRerouting:
    @pytest.mark.parametrize("name", ["uvicorn.error", "uvicorn.access", "arq.worker"])
    def test_known_loggers_propagate_with_no_direct_handlers(self, name: str) -> None:
        third_party = logging.getLogger(name)
        third_party.addHandler(logging.StreamHandler())
        third_party.propagate = False

        configure_logging()

        assert third_party.propagate is True
        assert third_party.handlers == []

    def test_reroute_runs_on_every_call_for_late_created_loggers(self) -> None:
        """A logger given its own handler after the first call is still re-routed."""
        configure_logging()
        late = logging.getLogger("arq.jobs")
        late.addHandler(logging.StreamHandler())
        late.propagate = False

        configure_logging()

        assert late.propagate is True
        assert late.handlers == []
