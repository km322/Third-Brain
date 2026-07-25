"""Aggregate the versioned API router from per-domain route modules.

Includes are resilient: if a single route module fails to import (e.g. mid-build), it is
logged and skipped rather than taking down the entire API. In production all modules are
present and this simply wires them together under ``/api/v1``.
"""

from __future__ import annotations

import importlib

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)

api_router = APIRouter()

# Order controls docs grouping only; each module owns its own prefix + tags.
_ROUTE_MODULES = [
    "health",
    "auth",
    "users",
    "orgs",
    "teams",
    "invites",
    "api_keys",
    "device_auth",
    "collections",
    "documents",
    "files",
    "answers",
    "entities",
    "conversations",
    "search",
    "graph",
    "connectors",
    "data_sources",
    "permissions",
    "governance",
    "feedback",
    "scim",
    "scim_tokens",
    "sso",
    "analytics",
    "waitlist",
]


def _load() -> None:
    for name in _ROUTE_MODULES:
        try:
            module = importlib.import_module(f"app.api.routes.{name}")
            api_router.include_router(module.router)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load route module '%s': %s", name, exc)


_load()
