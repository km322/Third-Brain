"""Structured logging configuration built on structlog.

All logging - structlog call sites and third-party stdlib loggers (uvicorn, gunicorn,
arq, sqlalchemy, alembic) alike - is routed through one stdout handler whose formatter
runs the same processor chain, so every line renders uniformly: pretty console output in
development, JSON in production (see ``settings.log_format_resolved``).

Libraries such as sqlalchemy and alembic already propagate to the root logger; uvicorn,
gunicorn and arq install their own handlers with ``propagate=False`` at import time, so
``configure_logging`` re-routes them (clearing those handlers and re-enabling
propagation) on every call to catch loggers created after the first call ran.

Call sites use ``get_logger(__name__)``; both classic ``%s`` formatting and structured
``logger.info("event_name", key=value)`` calls work.
"""

from __future__ import annotations

import logging
import re
import sys

import structlog
from opentelemetry import trace

from app.core.config import settings

_CONFIGURED = False

# Third-party loggers that install their own handlers with ``propagate=False`` at import
# time; re-routing them funnels their records through the shared root handler.
_THIRD_PARTY_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
    "arq",
    "arq.worker",
    "arq.jobs",
    "arq.connections",
)

_REDACTED_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def _is_metric_or_identifier(lowered: str) -> bool:
    """True for keys that carry ids/counts/measurements, never secret values.

    Identifiers end in ``_id``; measurements end in ``_ms``/``_count``. LLM token
    *counts* use the plural word ``tokens`` (``tokens_in``, ``max_tokens``, ...), which
    distinguishes them from a singular ``token`` credential, so new count fields stay
    safe without a per-key allowlist.
    """
    if lowered.endswith(("_id", "_ms", "_count")):
        return True
    return "tokens" in re.split(r"[^a-z0-9]+", lowered)


def redact_sensitive_values(
    logger: object, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Replace the value of any top-level key that looks like a credential.

    Identifier and metric keys (``*_id``, ``*_ms``, ``*_count``, and ``tokens*`` usage
    counts) are treated as safe and left intact; every other key whose name contains a
    credential marker has its value censored.
    """
    for key in event_dict:
        lowered = key.lower()
        if _is_metric_or_identifier(lowered):
            continue
        if any(marker in lowered for marker in _REDACTED_KEY_MARKERS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def add_trace_context(
    logger: object, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Stamp the active OpenTelemetry trace/span ids so logs correlate with traces."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _shared_processors() -> list:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_sensitive_values,
        add_trace_context,
        structlog.processors.UnicodeDecoder(),
    ]


def _renderer() -> structlog.typing.Processor:
    if settings.log_format_resolved == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty() or sys.stdout.isatty())


def _build_formatter(
    renderer: structlog.typing.Processor | None = None,
) -> structlog.stdlib.ProcessorFormatter:
    """Build the ``ProcessorFormatter`` shared by the root handler (and tests).

    ``foreign_pre_chain`` runs the shared processors on records from stdlib loggers so
    they render identically to structlog call sites; ``renderer`` defaults to the format
    chosen by ``settings.log_format_resolved`` (tests force the JSON renderer).
    """
    shared = _shared_processors()
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            p for p in shared if not isinstance(p, structlog.stdlib.PositionalArgumentsFormatter)
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer or _renderer(),
        ],
    )


def _reroute_third_party_loggers() -> None:
    """Funnel known third-party stdlib loggers through the shared root handler.

    uvicorn/gunicorn (API) and arq (worker) install their own plain-text handlers with
    ``propagate=False``, which would otherwise emit records separately from - and in a
    different format than - everything else. Clearing those handlers and re-enabling
    propagation makes every record render through the one root handler. This runs on each
    ``configure_logging`` call because these loggers may be created after the first call
    (uvicorn before ``app.main`` import, arq inside the worker), so it must be idempotent
    and catch late arrivals. Re-routed loggers keep their levels; the root level applies.
    """
    for name in _THIRD_PARTY_LOGGERS:
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True

    # Quiet noisy loggers unless we're debugging (levels only; handlers already cleared).
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def configure_logging() -> None:
    """Install the structlog + stdlib logging pipeline (idempotent).

    The handler and ``structlog.configure`` setup runs once; the third-party logger
    re-routing runs on every call so loggers created after the first call are still
    funneled through the root handler.
    """
    global _CONFIGURED
    if not _CONFIGURED:
        level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        shared = _shared_processors()

        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                *shared,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_build_formatter())
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)

        _CONFIGURED = True

    _reroute_third_party_loggers()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)
