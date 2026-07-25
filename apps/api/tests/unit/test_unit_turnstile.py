"""Unit tests for :func:`verify_turnstile` - the pure decision branches.

No network is touched: only the "disabled" and "missing token" paths are exercised, which
resolve before any HTTP call. The Cloudflare round-trip itself is covered implicitly by the
integration suite (which runs with verification disabled).
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.turnstile import verify_turnstile


async def test_disabled_when_no_secret_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", None, raising=False)
    # Even a missing token passes when the feature is off, so the form works with no setup.
    assert await verify_turnstile(None) is True
    assert await verify_turnstile("anything") is True


async def test_rejects_missing_token_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secret", raising=False)
    # A configured secret with no token means the widget was skipped - reject without a call.
    assert await verify_turnstile(None) is False
    assert await verify_turnstile("") is False
