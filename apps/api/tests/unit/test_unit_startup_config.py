"""Unit: the production startup guard on ``SECRET_KEY`` (`validate_startup_config`).

The guard is the only thing standing between a misconfigured deploy and a world-readable
JWT signing key, so it must reject the placeholder we actually ship in ``.env.example``
(which is long enough to pass the length check).
"""

from __future__ import annotations

import pytest

from app import main

# The exact value shipped in .env.example.
_SHIPPED_PLACEHOLDER = "change-me-to-a-long-random-string-please"


def test_production_rejects_shipped_env_example_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "SECRET_KEY", _SHIPPED_PLACEHOLDER)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        main.validate_startup_config()


def test_production_rejects_short_secret(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "SECRET_KEY", "too-short")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        main.validate_startup_config()


def test_production_accepts_a_strong_unique_secret(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "SECRET_KEY", "a-genuinely-random-" + "x" * 40)
    # A valid production config also needs a real DB password (see the guard below).
    monkeypatch.setattr(main.settings, "POSTGRES_PASSWORD", "a-strong-db-password")
    # Avoid touching the local upload dir on the test host.
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "s3")
    main.validate_startup_config()  # must not raise


def test_development_still_boots_with_the_placeholder(monkeypatch) -> None:
    # The guard is production-only; local dev must keep working with the shipped default.
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(main.settings, "SECRET_KEY", _SHIPPED_PLACEHOLDER)
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "s3")
    main.validate_startup_config()  # must not raise


def test_production_refuses_the_default_db_password(monkeypatch) -> None:
    # The bundled Postgres ships with a publicly-known default password; a deployed env that
    # derives its DB URL from the POSTGRES_* parts must refuse to boot on it, like SECRET_KEY.
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "SECRET_KEY", "a-genuinely-random-" + "x" * 40)
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(main.settings, "DATABASE_URL", None)
    monkeypatch.setattr(main.settings, "POSTGRES_PASSWORD", "thirdbrain")
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        main.validate_startup_config()


def test_explicit_database_url_bypasses_the_db_password_guard(monkeypatch) -> None:
    # An explicit DATABASE_URL carries its own credentials, so the bundled-Postgres default is
    # irrelevant and the guard must not fire.
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "SECRET_KEY", "a-genuinely-random-" + "x" * 40)
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(main.settings, "DATABASE_URL", "postgresql+asyncpg://u:p@db/app")
    monkeypatch.setattr(main.settings, "POSTGRES_PASSWORD", "thirdbrain")
    main.validate_startup_config()  # must not raise


def test_development_boots_with_the_default_db_password(monkeypatch) -> None:
    # The DB-password guard is deployed-only, like the SECRET_KEY one.
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(main.settings, "SECRET_KEY", _SHIPPED_PLACEHOLDER)
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(main.settings, "DATABASE_URL", None)
    monkeypatch.setattr(main.settings, "POSTGRES_PASSWORD", "thirdbrain")
    main.validate_startup_config()  # must not raise
