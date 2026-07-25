"""Integration: Alembic migration round-trip (upgrade head -> downgrade base -> upgrade head).

Proves the squashed baseline builds the complete model schema from an empty database,
tears it back down to nothing, and builds it again - the contract behind fresh installs
and ``make db-downgrade`` rollbacks. Runs in a dedicated scratch database so the shared
session schema (migrated once by ``integration_infra``) is never disturbed.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

HEAD_REVISION = "0001_initial"


def _public_tables(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        return {row[0] for row in rows}


def _version_rows(engine) -> list[str]:
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]


def test_migration_roundtrip(integration_infra, alembic_config_factory, monkeypatch) -> None:
    """Sync by design: the Alembic command API drives a synchronous psycopg engine."""
    import app.models  # noqa: F401 - registers every table on Base.metadata
    from alembic import command
    from app.core.config import settings
    from app.core.db import Base

    admin_url = make_url(settings.alembic_database_uri)
    scratch_db = f"tb_migration_roundtrip_{uuid.uuid4().hex[:8]}"

    # CREATE DATABASE cannot run inside a transaction, hence the AUTOCOMMIT engine.
    admin_engine = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
    except Exception:
        admin_engine.dispose()
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            raise
        pytest.skip("test role cannot CREATE DATABASE - skipping migration round-trip")

    scratch_engine = create_engine(admin_url.set(database=scratch_db), poolclass=NullPool)
    try:
        # alembic/env.py resolves its URL from app settings, so point settings at the
        # scratch DB too (monkeypatch restores the real test URL afterwards).
        scratch_async_url = admin_url.set(drivername="postgresql+asyncpg", database=scratch_db)
        monkeypatch.setattr(
            settings, "DATABASE_URL", scratch_async_url.render_as_string(hide_password=False)
        )
        cfg = alembic_config_factory(settings.alembic_database_uri)

        expected_tables = set(Base.metadata.tables.keys()) | {"alembic_version"}

        command.upgrade(cfg, "head")
        assert _version_rows(scratch_engine) == [HEAD_REVISION]
        assert _public_tables(scratch_engine) == expected_tables

        command.downgrade(cfg, "base")
        remaining = _public_tables(scratch_engine)
        assert remaining <= {"alembic_version"}
        if "alembic_version" in remaining:
            assert _version_rows(scratch_engine) == []

        command.upgrade(cfg, "head")
        assert _version_rows(scratch_engine) == [HEAD_REVISION]
        assert _public_tables(scratch_engine) == expected_tables
    finally:
        scratch_engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}" WITH (FORCE)'))
        admin_engine.dispose()
