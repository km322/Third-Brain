"""Model Context Protocol server package for Third Brain.

Exposes :func:`mount_mcp`, which mounts a JSON-RPC 2.0 MCP endpoint at ``/mcp`` on the
FastAPI application. See :mod:`app.mcp.server` for the transport and auth details, and
:mod:`app.mcp.tools` for the tool implementations.
"""

from __future__ import annotations

from app.mcp.server import mount_mcp

__all__ = ["mount_mcp"]
