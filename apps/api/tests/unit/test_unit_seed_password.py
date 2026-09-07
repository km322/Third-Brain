"""Unit: demo-password resolution - no well-known default may ever exist.

An explicit DEMO_PASSWORD is validated against the login policy (LoginRequest bounds
passwords to 8..128 characters; seeding outside that range would create demo accounts
that can never sign in). When unset, every seed generates a fresh random secret. The
resolution runs before any database access, so this is a pure unit test.
"""

from __future__ import annotations

import importlib

import pytest

import app.scripts.seed as seed_module


def test_explicit_out_of_range_password_is_rejected(monkeypatch) -> None:
    for bad in ("short", "x" * 129):
        monkeypatch.setattr(seed_module, "DEMO_PASSWORD", bad)
        with pytest.raises(SystemExit):
            seed_module.resolve_demo_password()


def test_explicit_password_is_used_verbatim(monkeypatch) -> None:
    monkeypatch.setattr(seed_module, "DEMO_PASSWORD", "a-strong-enough-secret")
    assert seed_module.resolve_demo_password() == ("a-strong-enough-secret", False)


def test_unset_password_generates_a_fresh_secret_each_time(monkeypatch) -> None:
    monkeypatch.setattr(seed_module, "DEMO_PASSWORD", None)
    first, generated = seed_module.resolve_demo_password()
    second, _ = seed_module.resolve_demo_password()
    assert generated is True
    assert seed_module.MIN_PASSWORD_LENGTH <= len(first) <= seed_module.MAX_PASSWORD_LENGTH
    assert first != second


async def test_invalid_demo_email_is_rejected_before_any_db_access(monkeypatch) -> None:
    for bad in ("not an email", "nodomain@", "@example.com", "missing.at.sign.com"):
        monkeypatch.setattr(seed_module, "ADMIN_EMAIL", bad)
        with pytest.raises(SystemExit):
            await seed_module.seed()


async def test_duplicate_demo_emails_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(seed_module, "ADMIN_EMAIL", "same@example.com")
    monkeypatch.setattr(seed_module, "VIEWER_EMAIL", "same@example.com")
    with pytest.raises(SystemExit):
        await seed_module.seed()


def test_empty_demo_password_env_means_unset(monkeypatch) -> None:
    """Compose env plumbing often exports unset vars as empty strings; an empty
    DEMO_PASSWORD must mean "generate a secret", never "seed an empty password"."""
    monkeypatch.setenv("DEMO_PASSWORD", "")
    try:
        reloaded = importlib.reload(seed_module)
        assert reloaded.DEMO_PASSWORD is None
    finally:
        monkeypatch.delenv("DEMO_PASSWORD", raising=False)
        importlib.reload(seed_module)
