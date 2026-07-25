"""FastAPI application factory for Third Brain.

Surfaces:
  * ``/api/v1``     - native REST API (auth, documents, search, admin, …)
  * ``/v1``         - OpenAI-compatible endpoint (chat/completions, embeddings)
  * ``/mcp``        - Model Context Protocol server (optional mount)
  * ``/health``     - liveness/readiness
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    AccessLogMiddleware,
    BodySizeLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.telemetry import instrument_app, setup_telemetry, shutdown_telemetry

configure_logging()
setup_telemetry("third-brain-api")
logger = get_logger(__name__)

# Secret keys we ship as placeholders and must never run in production.
_INSECURE_SECRETS = {"", "change-me", "changeme", "secret", "test-secret-key"}
# Also reject any secret that merely extends a shipped placeholder (e.g. the 40-char
# ``.env.example`` default ``change-me-to-a-long-random-string-please``), which is long
# enough to pass the length check but is not actually secret.
_INSECURE_SECRET_PREFIXES = ("change-me", "changeme")
_MIN_SECRET_LEN = 32


def validate_startup_config() -> None:
    """Fail fast on unsafe production configuration; warn on softer smells.

    * Refuses to boot when the environment is DEPLOYED (staging or production) and
      ``SECRET_KEY`` is a shipped placeholder or too short to be a real signing key.
    * Warns (does not block) on wildcard/permissive CORS, refusing to boot when deployed.
    """
    if settings.is_deployed:
        secret = settings.SECRET_KEY or ""
        if (
            secret in _INSECURE_SECRETS
            or len(secret) < _MIN_SECRET_LEN
            or secret.lower().startswith(_INSECURE_SECRET_PREFIXES)
        ):
            raise RuntimeError(
                f"Refusing to start in {settings.ENVIRONMENT}: SECRET_KEY is unset, a shipped "
                f"placeholder, or shorter than {_MIN_SECRET_LEN} characters. Set a "
                "strong, unique SECRET_KEY."
            )

        # Refuse to boot when the bundled Postgres is still on its shipped default password,
        # mirroring the SECRET_KEY guard above (a deployed env must never run a publicly-known
        # DB credential). Only meaningful when the app derives the DB URL from the POSTGRES_*
        # parts (an explicit DATABASE_URL carries its own credentials).
        if not settings.DATABASE_URL and settings.POSTGRES_PASSWORD == "thirdbrain":
            raise RuntimeError(
                f"Refusing to start in {settings.ENVIRONMENT}: POSTGRES_PASSWORD is the shipped "
                "default 'thirdbrain'. Set a strong, unique database password for the bundled "
                "Postgres (or point DATABASE_URL at a database with its own credentials)."
            )

    if "*" in settings.cors_origins:
        msg = (
            "CORS is configured to allow all origins ('*') together with credentialed "
            "requests -- this is permissive and unsafe."
        )
        if settings.is_deployed:
            # With allow_credentials=True, Starlette reflects any Origin back and sets
            # Access-Control-Allow-Credentials: true, so '*' lets any site make credentialed
            # cross-origin calls. Refuse to boot rather than ship that in a deployed env.
            raise RuntimeError(
                f"{msg} Set BACKEND_CORS_ORIGINS to an explicit allowlist when deployed."
            )
        logger.warning("%s", msg)

    # A live completion provider with no embeddings-capable key (e.g. only ANTHROPIC_API_KEY -
    # Anthropic has no embeddings API) would silently fall back to the offline stub for
    # embeddings and corrupt the vector store. Per-org connectors can still supply embeddings,
    # so warn here; the embedding call itself raises when it actually hits this fallback.
    if settings.EMBEDDING_PROVIDER not in ("fake", "offline"):
        from app.services.llm import effective_provider

        if (
            effective_provider(purpose="embedding") == "offline"
            and effective_provider(purpose="completion") != "offline"
        ):
            logger.warning(
                "A completion provider is configured but no embeddings-capable key "
                "(OPENAI_API_KEY or GOOGLE_API_KEY) is set; embeddings will fall back to the "
                "offline stub. Set one to enable real embeddings."
            )

    # Ensure the local upload directory exists so file/text ingestion can write to it.
    # The default (/data/uploads) is a mounted volume under docker-compose; when running
    # outside Docker, point STORAGE_LOCAL_PATH at a writable directory.
    if settings.STORAGE_BACKEND == "local":
        try:
            os.makedirs(settings.STORAGE_LOCAL_PATH, exist_ok=True)
        except OSError as exc:
            logger.error(
                "Local storage path %r is not writable (%s). Document ingestion will "
                "fail until STORAGE_LOCAL_PATH points at a writable directory (or the "
                "%r volume is mounted).",
                settings.STORAGE_LOCAL_PATH,
                exc,
                settings.STORAGE_LOCAL_PATH,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (%s)", settings.PROJECT_NAME, __version__, settings.ENVIRONMENT)

    # Reset any documents left mid-ingestion by a previous crash/deploy so they surface as
    # FAILED (and can be reprocessed) instead of being stranded in "processing" forever.
    try:
        from app.core.db import SessionLocal
        from app.services.ingestion import reap_stuck_documents

        async with SessionLocal() as db:
            await reap_stuck_documents(db)
    except Exception as exc:  # pragma: no cover - never block boot on the reaper
        logger.warning("Startup document reaper failed: %s", exc)

    yield
    from app.core.redis import get_redis
    from app.services.llm.client import aclose_llm_client

    try:
        await get_redis().aclose()
    except Exception:  # pragma: no cover
        pass
    try:
        await aclose_llm_client()
    except Exception:  # pragma: no cover
        pass
    shutdown_telemetry()
    logger.info("Shutting down")


def create_app() -> FastAPI:
    validate_startup_config()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=__version__,
        description="Company-wide, LLM-agnostic knowledge layer.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Middleware order matters: the *last* added is the outermost. We want the request
    # id bound before anything else runs (so every log line - access log included -
    # carries it), hence RequestIDMiddleware is registered last.
    # BodySizeLimit is registered first (innermost) so its 413 still flows back out through
    # the security-header, access-log and request-id layers.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    @app.get("/", tags=["health"])
    async def root() -> dict:
        return {
            "name": settings.PROJECT_NAME,
            "version": __version__,
            "docs": "/docs",
            "surfaces": ["/api/v1", "/v1", "/mcp"],
        }

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # OpenAI-compatible surface (defines its own /v1 prefix). Optional during build.
    try:
        from app.api.openai_compat import router as openai_router

        app.include_router(openai_router)
    except Exception as exc:  # pragma: no cover
        logger.warning("OpenAI-compatible endpoint not mounted: %s", exc)

    # MCP server mount. Optional during build.
    try:
        from app.mcp import mount_mcp

        mount_mcp(app)
    except Exception as exc:  # pragma: no cover
        logger.warning("MCP server not mounted: %s", exc)

    instrument_app(app)

    return app


app = create_app()
