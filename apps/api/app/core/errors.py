"""Consistent JSON error envelope + exception handlers.

Every error response the API returns shares one flat shape::

    {
      "detail":     <original message | structured detail>,
      "code":       "not_found",          # machine-readable slug
      "status":     404,
      "request_id": "9f2c…",              # correlate with logs
      "errors":     [ … ]                  # only for request validation failures
    }

``detail`` and ``code`` are kept at the top level for backwards compatibility with the
dashboard client (``apps/web/lib/api.ts`` reads ``data.detail`` / ``data.code``). The
500 handler hides internals in production while always logging the traceback under the
request id so failures stay diagnosable.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.logging import get_logger
from app.core.middleware import current_request_id, scrub_capability_path

logger = get_logger(__name__)


def _request_id(request: Request) -> str:
    """Resolve the correlation id, preferring request state then the context var."""
    rid = getattr(request.state, "request_id", None)
    if rid:
        return rid
    rid = current_request_id()
    return rid if rid and rid != "-" else "unknown"


def _code_for_status(status_code: int) -> str:
    """Machine-readable slug for a status code, e.g. 404 -> ``not_found``."""
    try:
        return HTTPStatus(status_code).name.lower()
    except ValueError:
        return "http_error"


def _envelope(
    *,
    status_code: int,
    detail: object,
    code: str,
    request_id: str,
    extra: dict | None = None,
) -> dict:
    body: dict = {
        "detail": detail,
        "code": code,
        "status": status_code,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
    return body


def _is_openai_path(request: Request) -> bool:
    """The OpenAI-compatible surface lives at ``/v1`` (outside ``/api/v1``)."""
    return request.url.path.startswith("/v1/") or request.url.path == "/v1"


def _openai_error_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "api_error"
    return "invalid_request_error"


def _openai_error_response(*, status_code: int, message: object, request_id: str) -> JSONResponse:
    """OpenAI-shaped error body so SDKs/wrappers pointed at ``/v1`` parse errors correctly.

    OpenAI clients read ``err.body["error"]["message"]`` / ``["type"]``; the flat Third
    Brain envelope has no ``error`` key, so they surface blank or misclassified errors.
    OpenAI also never emits 422, so caller validation failures are mapped to 400 here.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message if isinstance(message, str) else str(message),
                "type": _openai_error_type(status_code),
                "param": None,
                "code": None,
            }
        },
        headers={"X-Request-ID": request_id},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = _request_id(request)
    if _is_openai_path(request):
        resp = _openai_error_response(
            status_code=exc.status_code, message=exc.detail, request_id=request_id
        )
        for k, v in (getattr(exc, "headers", None) or {}).items():
            resp.headers.setdefault(k, v)
        return resp
    body = _envelope(
        status_code=exc.status_code,
        detail=exc.detail,
        code=_code_for_status(exc.status_code),
        request_id=request_id,
    )
    headers = dict(getattr(exc, "headers", None) or {})
    headers.setdefault("X-Request-ID", request_id)
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = _request_id(request)
    if _is_openai_path(request):
        # Map validation failures to 400 (OpenAI never returns 422) in the OpenAI shape.
        return _openai_error_response(
            status_code=HTTPStatus.BAD_REQUEST,
            message="Invalid request: " + "; ".join(_summarize_validation(exc)),
            request_id=request_id,
        )
    body = _envelope(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        detail="Request validation failed",
        code="validation_error",
        request_id=request_id,
        extra={"errors": jsonable_encoder(exc.errors())},
    )
    return JSONResponse(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        content=body,
        headers={"X-Request-ID": request_id},
    )


def _summarize_validation(exc: RequestValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        out.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else err.get("msg", "invalid"))
    return out or ["validation failed"]


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.exception(
        "Unhandled error on %s %s [request_id=%s]",
        request.method,
        scrub_capability_path(request.url.path),
        request_id,
    )
    # Never leak exception internals to clients in a deployed env (staging or production).
    detail = "Internal server error" if settings.is_deployed else f"{type(exc).__name__}: {exc}"
    if _is_openai_path(request):
        return _openai_error_response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            message=detail,
            request_id=request_id,
        )
    body = _envelope(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        detail=detail,
        code="internal_error",
        request_id=request_id,
    )
    return JSONResponse(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        content=body,
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the envelope handlers onto the application (call from ``create_app``)."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
