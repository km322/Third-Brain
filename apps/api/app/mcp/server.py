"""Model Context Protocol (MCP) server for Third Brain.

Exposes the knowledge base to MCP-native clients (Claude Desktop, Cursor, and agent
frameworks) so they can search, read and write knowledge with the same permissions the
REST API enforces.

Transport
---------
The official ``mcp`` Python SDK ships an SSE / streamable-HTTP server transport, but its
ASGI-mounting surface has shifted across releases and is not guaranteed to be importable
in every deployment. To stay self-contained and to never raise at import/mount time, this
module implements the MCP wire protocol directly as a **JSON-RPC 2.0 endpoint** - which is
exactly the streamable-HTTP transport's message format - over a single ``POST /mcp`` route
(the server responds with ``application/json`` rather than an SSE stream). A convenience
``GET /mcp`` returns a non-authenticated descriptor of the server.

Supported JSON-RPC methods: ``initialize``, ``notifications/initialized`` (and other
notifications, ignored), ``ping``, ``tools/list``, ``tools/call``. Tools are defined in
:mod:`app.mcp.tools`.

Authentication
--------------
Every ``tools/*`` call is authenticated from the HTTP ``Authorization: Bearer tb_...``
header (an API key), resolved through :func:`app.core.deps.get_auth_context` - the same
code path the REST API and the OpenAI-compatible endpoint use. This yields an
:class:`~app.core.deps.AuthContext` bound to a single organization; org isolation and ACLs
are then enforced by the permission engine inside each tool. Per-key rate limits are
applied via :func:`app.core.deps.enforce_rate_limit` (failing open only when the limiter's
backing store is unreachable, so the server still works offline).

Connecting a client
--------------------
Point an MCP client at ``https://<host>/mcp`` with an ``Authorization: Bearer tb_...``
header. For stdio-only clients (e.g. Claude Desktop) bridge with ``mcp-remote``::

    {
      "mcpServers": {
        "third-brain": {
          "command": "npx",
          "args": [
            "mcp-remote", "https://<host>/mcp",
            "--header", "Authorization: Bearer tb_your_api_key"
          ]
        }
      }
    }

The API key's scopes gate what is possible on top of the permission engine's ACLs:
searching (``search_knowledge``) requires a ``search`` scope; writing (``add_knowledge`` /
``update_knowledge``) requires an ``ingest`` (or ``write``) scope plus ``editor`` permission
on the target. ``get_document`` / ``list_collections`` reads are governed by ACLs alone.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, enforce_rate_limit, get_auth_context
from app.core.logging import get_logger
from app.mcp.tools import TOOL_DEFINITIONS, ToolError, dispatch_tool

logger = get_logger(__name__)

# Protocol constants
JSONRPC_VERSION = "2.0"
# MCP protocol revisions this server actually implements. The client's requested version is
# echoed back only when we support it; otherwise we answer with our latest supported version,
# per the spec's version-negotiation rules.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INFO = {"name": "third-brain", "version": __version__}

# JSON-RPC error codes (subset) + a private code for auth failures.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAUTHORIZED = -32001


class _AuthError(Exception):
    """Raised when a request cannot be authenticated for a data-touching method."""


# --------------------------------------------------------------------------- #
# JSON-RPC envelope helpers
# --------------------------------------------------------------------------- #
def _result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "error": err}


def _tool_result(msg_id: Any, structured: dict[str, Any]) -> dict[str, Any]:
    """A successful ``tools/call`` result: human-readable text + structured payload."""
    text = json.dumps(structured, indent=2, ensure_ascii=False)
    return _result(
        msg_id,
        {
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured,
            "isError": False,
        },
    )


def _tool_error(msg_id: Any, message: str) -> dict[str, Any]:
    """A tool-domain failure surfaced as a result so the model can read it."""
    return _result(
        msg_id,
        {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True},
    )


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
async def _authenticate(request: Request, db: AsyncSession) -> AuthContext:
    """Resolve the caller from the request's API key header; enforce rate limits."""
    authorization = request.headers.get("authorization")
    x_api_key = request.headers.get("x-api-key")
    try:
        ctx = await get_auth_context(request, db, authorization=authorization, x_api_key=x_api_key)
    except HTTPException as exc:
        raise _AuthError(
            exc.detail if isinstance(exc.detail, str) else "Not authenticated"
        ) from exc

    try:
        await enforce_rate_limit(ctx)
    except HTTPException as exc:  # 429 - genuine limit hit
        raise _AuthError(
            exc.detail if isinstance(exc.detail, str) else "Rate limit exceeded"
        ) from exc
    except Exception as exc:  # pragma: no cover - limiter backend unavailable: fail open
        logger.warning("MCP rate-limit check skipped: %s", exc)
    return ctx


# --------------------------------------------------------------------------- #
# Method handling
# --------------------------------------------------------------------------- #
def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    # Only affirm a version we actually implement; otherwise negotiate down to our latest.
    negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": negotiated,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "Third Brain is your team's shared knowledge base, exposed over MCP. Use "
            "search_knowledge to retrieve, get_document to read full text, and "
            "list_collections to browse.\n\n"
            "Capture knowledge back proactively as you work - you do NOT need to be asked. "
            "Whenever you make a decision, resolve a tradeoff, solve a problem, or produce a "
            "reusable answer or piece of documentation during a task, immediately record it "
            "with add_knowledge (or update_knowledge to revise an existing document). You can "
            "omit the collection - Third Brain files it in the best-matching collection "
            "automatically - and set doc_type (e.g. 'decision') to categorize it. Keep each "
            "entry concise and self-contained: what was decided or learned, and why. Do this "
            "by default so the team's documentation stays current; the user should not have "
            "to ask you to write things up. All access is permission-scoped."
        ),
    }


async def _handle_message(
    message: Any, request: Request, db: AsyncSession
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns a response dict, or ``None`` for notifications."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "Invalid Request: message must be an object")

    msg_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")

    if not isinstance(method, str):
        if is_notification:
            return None
        return _error(msg_id, INVALID_REQUEST, "Invalid Request: missing 'method'")

    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "Invalid params: expected an object")

    # JSON-RPC 2.0: a message with no ``id`` is a Notification, and the server MUST NOT reply
    # to it - including for otherwise-request methods (initialize/ping/tools.*) sent id-less.
    if is_notification:
        return None

    if method == "initialize":
        return _result(msg_id, _initialize_result(params))

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": TOOL_DEFINITIONS})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(msg_id, INVALID_PARAMS, "Invalid params: 'name' is required")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "Invalid params: 'arguments' must be an object")

        try:
            ctx = await _authenticate(request, db)
        except _AuthError as exc:
            return _error(msg_id, UNAUTHORIZED, str(exc))

        try:
            structured = await dispatch_tool(db, ctx, name, arguments)
            await db.commit()
            return _tool_result(msg_id, structured)
        except (ToolError, HTTPException) as exc:
            await db.rollback()
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return _tool_error(msg_id, str(detail))
        except Exception as exc:  # pragma: no cover - unexpected tool failure
            await db.rollback()
            logger.exception("MCP tool '%s' failed", name)
            # Never leak internal exception text to clients in production (parity with the
            # REST 500 handler); the traceback is in the logs under this request.
            detail = (
                "Internal error running the tool."
                if settings.is_production
                else f"Internal error running tool: {exc}"
            )
            return _tool_error(msg_id, detail)

    return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #
async def _post_mcp(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """JSON-RPC 2.0 entrypoint for the MCP streamable-HTTP transport."""
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return JSONResponse(_error(None, PARSE_ERROR, "Parse error"), status_code=200)

    if payload is None:
        return JSONResponse(_error(None, INVALID_REQUEST, "Empty request"), status_code=200)

    # Batch request.
    if isinstance(payload, list):
        if not payload:
            return JSONResponse(_error(None, INVALID_REQUEST, "Empty batch"), status_code=200)
        responses: list[dict[str, Any]] = []
        for item in payload:
            resp = await _handle_message(item, request, db)
            if resp is not None:
                responses.append(resp)
        if not responses:  # all notifications
            return Response(status_code=202)
        return JSONResponse(responses, status_code=200)

    # Single request.
    response = await _handle_message(payload, request, db)
    if response is None:  # a notification
        return Response(status_code=202)
    return JSONResponse(response, status_code=200)


async def _get_mcp() -> JSONResponse:
    """Unauthenticated descriptor of the MCP server for discovery/health checks."""
    return JSONResponse(
        {
            "server": SERVER_INFO,
            "protocol": "mcp",
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "transport": "streamable-http (json-rpc 2.0 over POST)",
            "endpoint": "/mcp",
            "authentication": "Authorization: Bearer tb_... (Third Brain API key)",
            "methods": ["initialize", "ping", "tools/list", "tools/call"],
            "tools": [t["name"] for t in TOOL_DEFINITIONS],
        }
    )


def mount_mcp(app: FastAPI) -> None:
    """Mount the MCP server at ``/mcp`` on ``app``.

    Defensive by contract: this never raises. ``app.main`` also wraps the call in a
    try/except, but keeping it self-contained means a partially-built environment can
    still boot the rest of the API even if mounting fails here.
    """
    try:
        app.add_api_route(
            "/mcp", _post_mcp, methods=["POST"], include_in_schema=False, name="mcp_rpc"
        )
        app.add_api_route(
            "/mcp", _get_mcp, methods=["GET"], include_in_schema=False, name="mcp_info"
        )
        logger.info(
            "MCP server mounted at /mcp (tools: %s)", ", ".join(t["name"] for t in TOOL_DEFINITIONS)
        )
    except Exception as exc:  # pragma: no cover - never break app startup
        logger.warning("MCP server not mounted: %s", exc)
