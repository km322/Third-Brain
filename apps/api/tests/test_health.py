"""Smoke tests for the ASGI app's always-on surfaces.

These use an ``AsyncClient`` bound to the real app but do **not** require a database:
the ``/health`` probe is designed to report ``degraded`` (still HTTP 200) when its
dependencies are unreachable, so these run in every environment.
"""

from __future__ import annotations

from app import __version__
from app.core.config import settings


class TestHealth:
    async def test_health_probe_returns_structured_status(self, raw_client) -> None:
        resp = await raw_client.get(f"{settings.API_V1_PREFIX}/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in {"ok", "degraded"}
        assert body["version"] == __version__
        checks = body["checks"]
        assert set(checks) == {"database", "redis"}
        assert isinstance(checks["database"], bool)
        assert isinstance(checks["redis"], bool)

    async def test_version_reports_build_metadata(self, raw_client) -> None:
        resp = await raw_client.get(f"{settings.API_V1_PREFIX}/version")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == __version__
        assert set(body) == {"version", "git_commit", "build_time", "environment"}

    async def test_root_advertises_surfaces(self, raw_client) -> None:
        resp = await raw_client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == settings.PROJECT_NAME
        assert body["version"] == __version__
        assert "/api/v1" in body["surfaces"]

    async def test_openapi_schema_is_served(self, raw_client) -> None:
        resp = await raw_client.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["info"]["title"] == settings.PROJECT_NAME
