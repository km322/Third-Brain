"""ASGI middleware for request correlation, security headers and access logging.

Three concerns, kept as thin *pure-ASGI* middlewares (not ``BaseHTTPMiddleware``) so
that the request-id :class:`~contextvars.ContextVar` propagates reliably into route
handlers and the logging subsystem:

* :class:`RequestIDMiddleware` - accept an inbound ``X-Request-ID`` (or mint one),
  expose it on ``request.state.request_id``, bind it to a context var and to the
  structlog contextvars (so every log line emitted while handling the request carries
  it) and echo it back on the response.
* :class:`SecurityHeadersMiddleware` - attach a conservative set of hardening headers
  (``X-Content-Type-Options``, ``X-Frame-Options``, ``Referrer-Policy`` always;
  ``Strict-Transport-Security`` only in production over the standard HTTPS assumption).
* :class:`AccessLogMiddleware` - one structured line per request with method, path,
  status and ``duration_ms`` (``request_id`` and auth context ride along via the
  structlog contextvars).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import get_logger

__all__ = [
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "AccessLogMiddleware",
    "BodySizeLimitMiddleware",
    "request_id_ctx",
    "current_request_id",
    "REQUEST_ID_HEADER",
]

REQUEST_ID_HEADER = "x-request-id"

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
"""Default sentinel so log lines emitted outside any request still format cleanly."""

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,200}$")
"""Conservative allow-list for inbound ids: prevents header/log injection while still
accepting the common uuid / trace-id shapes callers propagate."""

_access_logger = get_logger("app.access")


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _sanitize_request_id(value: str | None) -> str:
    if value:
        value = value.strip()
        if _SAFE_ID_RE.match(value):
            return value
    return _new_request_id()


def current_request_id() -> str:
    """Return the request id bound to the current context, or ``"-"`` if none."""
    return request_id_ctx.get()


class RequestIDMiddleware:
    """Bind a stable request id for the lifetime of each HTTP request.

    The id is written into the ASGI ``state`` so it is exposed to route handlers and to
    anything else reading ``request.state``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = _sanitize_request_id(inbound)

        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_ctx.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_ctx.reset(token)


class SecurityHeadersMiddleware:
    """Attach hardening headers to every HTTP response.

    HSTS is sent only in production: sending it in dev would poison localhost over http.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._base = {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
        }
        self._hsts = (
            "max-age=63072000; includeSubDomains; preload" if settings.is_production else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self._base.items():
                    headers.setdefault(key, value)
                if self._hsts is not None:
                    headers.setdefault("strict-transport-security", self._hsts)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Reject a request whose body exceeds ``max_bytes`` before a handler buffers it.

    JSON handlers read the whole body into memory (``await request.json()``), so without a cap
    a single large (even unauthenticated) request is a memory-exhaustion DoS. A declared
    ``Content-Length`` over the cap is rejected up front with 413; for chunked bodies with no
    length, a running byte count signals a disconnect once the cap is crossed so the handler
    aborts with bounded memory. Multipart uploads spool to disk, but the cap (well above the
    upload limit) still bounds them.

    A malformed ``Content-Length`` header falls through to that same streaming guard, which
    stops feeding the oversized body so the handler's read aborts with what it already has.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b"") or b"")
                if received > self.max_bytes:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)

    async def _reject(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": (
                    b'{"detail":"Request body too large","code":"request_entity_too_large",'
                    b'"status":413}'
                ),
            }
        )


_CAPABILITY_PATH_RE = re.compile(r"(/files/)[^/?#]+")
"""The capability token in ``/files/{token}`` IS the credential for those bytes, so it must
never reach the access log (or any log shipper downstream)."""


def scrub_capability_path(path: str) -> str:
    """Replace capability-token path segments so credentials never reach logs."""
    return _CAPABILITY_PATH_RE.sub(r"\1{token}", path)


class AccessLogMiddleware:
    """Emit one structured access-log line per request with timing.

    When the application raises, the response is a 500 synthesized upstream; the timing
    line is logged here and the exception re-raised.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "-")
        path = scrub_capability_path(scope.get("path", "-"))
        client = scope.get("client")
        client_ip = client[0] if client else "-"
        status_code = 500
        start = time.perf_counter()

        async def send_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self._emit(method, path, 500, duration_ms, client_ip, logging.ERROR)
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000.0
            level = logging.WARNING if status_code >= 500 else logging.INFO
            self._emit(method, path, status_code, duration_ms, client_ip, level)

    @staticmethod
    def _emit(
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        level: int,
    ) -> None:
        _access_logger.log(
            level,
            "http_request",
            method=method,
            path=path,
            status=status_code,
            duration_ms=round(duration_ms, 2),
            client=client_ip,
        )
