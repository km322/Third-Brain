"""Shared pytest fixtures for the Third Brain API test-suite.

The suite has two tiers:

* **Pure-unit tests** (security primitives, permission math, chunking, the health
  probe's shape) need no external infrastructure and always run.
* **Integration tests** drive the real FastAPI app through ``httpx.AsyncClient`` +
  ``ASGITransport`` against a live Postgres + pgvector database. They are guarded by the
  :func:`client` fixture, which creates the schema on demand and **skips** (never fails)
  when the database is unreachable -- so ``pytest`` stays green with no infrastructure
  running.

Everything runs fully offline: the LLM facade (``app.services.llm``) falls back to a
deterministic ``fake`` provider whenever no provider API keys are configured, so
embeddings and completions never touch the network during tests.
"""

from __future__ import annotations

import os
import pathlib
import uuid

# Pin the deterministic, offline LLM provider *before* the app settings are constructed
# so no test can accidentally reach a live provider. Defaults already omit keys; this
# just makes the intent explicit and stable across environments.
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

import pytest

from app.core.config import settings

# Belt-and-suspenders: force the live settings object into offline mode regardless of
# how (or whether) it read the environment.
settings.EMBEDDING_PROVIDER = "fake"
settings.OPENAI_API_KEY = None
# Keep the suite hermetic even when the developer's shell exports an OTLP endpoint
# (e.g. a local Grafana stack): tests assume tracing is disabled unless they install
# their own provider.
settings.OTEL_EXPORTER_OTLP_ENDPOINT = None

# --------------------------------------------------------------------------- #
# Bind the app to the *test* infrastructure BEFORE anything imports
# ``app.core.db`` (which builds the async engine at import time) or
# ``app.core.redis``. Integration tests resolve their Postgres/Redis endpoints
# from the ``TEST_*`` variables first, then the plain ``DATABASE_URL`` /
# ``REDIS_URL`` (as CI sets), and otherwise fall back to the settings defaults -
# which point at the docker-compose service hostnames and are therefore
# unreachable on a bare workstation, so the whole integration tier self-skips and
# a plain local ``pytest`` stays green. This is production-parity: real Postgres +
# pgvector and real Redis, never SQLite / fakeredis / in-memory shims.
# --------------------------------------------------------------------------- #
_TEST_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
if _TEST_DB_URL:
    settings.DATABASE_URL = _TEST_DB_URL
_TEST_REDIS_URL = os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")
if _TEST_REDIS_URL:
    settings.REDIS_URL = _TEST_REDIS_URL


# --------------------------------------------------------------------------- #
# Small helpers reused across tests
# --------------------------------------------------------------------------- #
def _unique_email(prefix: str = "user") -> str:
    """A globally-unique email so repeated runs never collide on the users table."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


async def _reset_engine_pool() -> None:
    """Dispose the shared engine so the next connection binds to the current loop.

    pytest-asyncio runs each test in its own event loop, and asyncpg connections are
    bound to the loop that created them; a pooled connection carried over from a
    previous test's loop would raise if reused. Disposing keeps every pool loop-local.
    """
    from app.core.db import engine

    await engine.dispose()


async def _reset_redis() -> None:
    """Drop the process-wide async Redis client so the next call rebinds to the loop.

    redis-py's async client caches connections against the loop that created them, so
    (like the DB engine) it must be reset between per-test event loops to avoid
    "attached to a different loop" errors.
    """
    import contextlib

    import app.core.redis as redis_mod

    if redis_mod._redis is not None:
        with contextlib.suppress(Exception):  # pragma: no cover - best-effort teardown
            await redis_mod._redis.aclose()
        redis_mod._redis = None


# --------------------------------------------------------------------------- #
# Integration-tier infrastructure gate (real Postgres + pgvector and real Redis)
# --------------------------------------------------------------------------- #
def _database_reachable() -> bool:
    """Probe the resolved Postgres synchronously (psycopg); no side effects."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    try:
        eng = create_engine(settings.alembic_database_uri, poolclass=NullPool)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


def _redis_reachable() -> bool:
    """Probe the resolved Redis synchronously; no side effects."""
    try:
        import redis as sync_redis

        client = sync_redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


def _alembic_config(url: str):
    """Build an Alembic ``Config`` for this repo's migration scripts, pointed at ``url``.

    Note that ``alembic/env.py`` resolves the migration URL from app settings, not from
    this Config, so callers must also point ``settings.DATABASE_URL`` at the target DB.
    """
    from alembic.config import Config

    api_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    # Escape '%' for ConfigParser interpolation (see alembic/env.py) so a DB URL containing a
    # percent-encoded credential doesn't crash the test upgrade.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _alembic_upgrade_head() -> None:
    """Run a real ``alembic upgrade head`` against the resolved test database.

    Uses Alembic's Python API (in-process, synchronous psycopg engine) so schema
    creation matches production exactly - no ``create_all`` shortcut. Idempotent:
    a database already at head is a no-op, so it is safe to run even when CI has
    already migrated the database in a prior step.
    """
    from alembic import command

    command.upgrade(_alembic_config(settings.alembic_database_uri), "head")


@pytest.fixture(scope="session", autouse=True)
def _allow_tmp_connector_roots(tmp_path_factory) -> str:
    """Allow the reference local_folder connector to read from the pytest tmp tree.

    Production defaults ``LOCAL_CONNECTOR_ROOTS`` to empty, which DISABLES the connector so a
    tenant admin can never point it at arbitrary server paths. The integration suite drives
    the connector against per-test ``tmp_path`` directories, all of which live under the
    session base temp dir - allow-list exactly that, nothing broader.
    """
    settings.LOCAL_CONNECTOR_ROOTS = str(tmp_path_factory.getbasetemp())
    return settings.LOCAL_CONNECTOR_ROOTS


@pytest.fixture(scope="session")
def alembic_config_factory():
    """The :func:`_alembic_config` helper, for tests that migrate their own scratch DB."""
    return _alembic_config


@pytest.fixture(scope="session")
def integration_infra() -> object:
    """Session-scoped gate for the integration tier.

    Skips (never fails) the entire tier when Postgres **or** Redis is unreachable, so a
    bare ``pytest`` with no infrastructure stays green. When both are up it applies the
    real migrations once for the whole session. Synchronous by design: it runs at
    session setup outside any per-test event loop.

    On CI the environment sets ``REQUIRE_INTEGRATION=1``: the tier then FAILS (not skips)
    when infra is unreachable, so a probe regression can never silently downgrade the
    authoritative gate to unit-tests-only while CI still reports green.
    """
    require = os.getenv("REQUIRE_INTEGRATION") == "1"

    def _gate(reachable: bool, name: str) -> None:
        if reachable:
            return
        message = f"{name} not reachable - skipping integration tier"
        if require:
            pytest.fail(
                f"REQUIRE_INTEGRATION=1 but {name} is unreachable: the integration tier "
                "MUST run here (this is the authoritative gate)."
            )
        pytest.skip(message)

    _gate(_database_reachable(), "Postgres+pgvector")
    _gate(_redis_reachable(), "Redis")
    _alembic_upgrade_head()
    return True


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
@pytest.fixture
async def raw_client():
    """An ``AsyncClient`` bound to the ASGI app with **no** database requirement.

    Used by tests that must pass even when no infrastructure is available -- e.g. the
    health probe, which is designed to report ``degraded`` rather than error when its
    dependencies are down.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    await _reset_engine_pool()


@pytest.fixture
async def db_ready(integration_infra):
    """A clean, migrated Postgres + pgvector schema for a single integration test.

    The schema itself is created once per session by :func:`integration_infra`
    (``alembic upgrade head``); this fixture just gives every test a blank slate by
    truncating all tables (``RESTART IDENTITY CASCADE``) and flushing Redis, then
    rebinds the engine + Redis client to the current event loop. It depends on
    :func:`integration_infra`, so it inherits the tier's skip-when-unreachable
    behaviour - a plain local ``pytest`` never fails here, it skips.
    """
    from sqlalchemy import text

    import app.models  # noqa: F401 - registers every table on Base.metadata
    from app.core.db import Base, engine

    await _reset_engine_pool()
    await _reset_redis()

    # Blank slate: truncate every mapped table (skip Alembic's version table).
    table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if table_names:
            await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))

    # Clear rate-limit buckets + embedding cache so each test is deterministic.
    try:
        from app.core.redis import get_redis

        await get_redis().flushdb()
    except Exception:  # pragma: no cover - cache reset is best-effort
        pass

    yield engine

    await _reset_engine_pool()
    await _reset_redis()


@pytest.fixture
async def client(db_ready, tmp_path, monkeypatch):
    """A DB-backed ``AsyncClient`` for integration tests.

    On top of :func:`db_ready` it:

    * points blob storage at a throwaway temp directory so uploads never touch the
      configured ``/data`` path, and
    * neutralises the fire-and-forget ingestion enqueue in the documents route, so tests
      drive ingestion explicitly and deterministically (via the ``ingest_now`` fixture)
      rather than racing a background task.
    """
    from httpx import ASGITransport, AsyncClient

    import app.services.storage as storage_mod
    from app.main import app

    # Route stored bytes to a temp dir instead of the configured /data path.
    storage_mod._storage = storage_mod.LocalStorage(str(tmp_path))

    async def _no_enqueue(doc_id):  # noqa: ANN001 - matches enqueue_ingest signature
        return False

    monkeypatch.setattr("app.api.routes.documents.enqueue_ingest", _no_enqueue)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    storage_mod._storage = None


# --------------------------------------------------------------------------- #
# Convenience fixtures for integration tests
# --------------------------------------------------------------------------- #
@pytest.fixture
def api() -> str:
    """The versioned API prefix (e.g. ``/api/v1``)."""
    return settings.API_V1_PREFIX


@pytest.fixture
def register(api):
    """Return an async helper that registers a fresh user + org and returns its session.

    The returned dict carries ``email``, ``password``, the raw ``tokens`` payload and a
    ready-to-use ``headers`` dict bearing the access token.
    """

    async def _register(
        http_client,
        *,
        email: str | None = None,
        password: str = "Sup3rSecret!",
        full_name: str = "Test User",
        org_name: str | None = None,
    ) -> dict:
        email = email or _unique_email()
        org_name = org_name or f"Org {uuid.uuid4().hex[:8]}"
        resp = await http_client.post(
            f"{api}/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": full_name,
                "org_name": org_name,
            },
        )
        assert resp.status_code == 201, resp.text
        tokens = resp.json()
        return {
            "email": email,
            "password": password,
            "tokens": tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    return _register


@pytest.fixture
def ingest_now():
    """Return an async helper that runs the ingestion pipeline inline for a document.

    Uses a dedicated session (the request session is already closed by the time tests
    call this) so the document is deterministically extracted, chunked and embedded
    before search assertions run.
    """

    async def _ingest(document_id) -> None:
        from app.core.db import SessionLocal
        from app.services.ingestion import ingest_document

        async with SessionLocal() as session:
            await ingest_document(session, uuid.UUID(str(document_id)))

    return _ingest


@pytest.fixture
async def db_session(db_ready):
    """A raw :class:`AsyncSession` bound to the test DB, for building state via factories.

    Depends on :func:`db_ready` so it shares the same clean slate as the ``client``
    fixture within a test (both cache off the single ``db_ready`` instance, so the
    truncation runs exactly once).
    """
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        yield session


@pytest.fixture
def token_headers():
    """Return a helper that mints a Bearer access token for a (user, org) pair.

    Lets tests authenticate as any factory-created user without going through the
    login route - the JWT is minted exactly the way :mod:`app.services.auth_service`
    does for the real endpoints. ``token_version`` defaults to 0, matching a
    freshly-created factory user; pass the user's current version after credential
    events (password change/reset) bump it.
    """
    from app.services import auth_service

    def _headers(user_id, org_id, token_version: int = 0) -> dict[str, str]:
        tokens = auth_service.make_tokens(
            uuid.UUID(str(user_id)), uuid.UUID(str(org_id)), token_version
        )
        return {"Authorization": f"Bearer {tokens.access_token}"}

    return _headers
