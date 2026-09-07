from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.core.config import settings
from app.core.db import engine
from app.core.redis import get_redis

router = APIRouter(tags=["health"])


async def _check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


@router.get("/health")
async def health() -> dict:
    """Liveness + dependency readiness probe (always HTTP 200)."""
    db_ok = await _check_db()
    redis_ok = await _check_redis()

    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "version": __version__,
        "checks": {"database": db_ok, "redis": redis_ok},
    }


@router.get("/health/live")
async def liveness() -> dict:
    """Cheap liveness probe: the process is up and serving. No dependency checks."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness probe: 200 only when every dependency is reachable, else 503.

    Suitable for a Kubernetes ``readinessProbe`` / load-balancer health check: while a
    dependency is down the pod is pulled from rotation rather than served traffic.
    """
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    ready = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": {"database": db_ok, "redis": redis_ok},
        },
    )


@router.get("/version")
async def version() -> dict:
    """Build metadata for the running deployment (version, commit, build time, env)."""
    return {
        "version": __version__,
        "git_commit": settings.GIT_COMMIT,
        "build_time": settings.BUILD_TIME,
        "environment": settings.ENVIRONMENT,
    }
