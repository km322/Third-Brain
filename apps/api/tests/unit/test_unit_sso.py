"""Unit tests for SSO OIDC id_token claim validation (no infra).

The live JWKS-signature path needs a real IdP and is covered by integration/deployment;
these exercise the pure claim checks that run on every OIDC login regardless of transport.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.sso import SsoError, _validate_id_token_claims


def _exp(delta_seconds: int) -> int:
    return int((datetime.now(UTC) + timedelta(seconds=delta_seconds)).timestamp())


class TestOidcClaimValidation:
    def test_accepts_valid_claims(self) -> None:
        _validate_id_token_claims(
            {"exp": _exp(300), "aud": "client-abc", "iss": "https://idp"},
            client_id="client-abc",
            issuer="https://idp",
        )

    def test_rejects_missing_exp(self) -> None:
        with pytest.raises(SsoError):
            _validate_id_token_claims({"aud": "client-abc"}, client_id="client-abc", issuer=None)

    def test_rejects_expired(self) -> None:
        with pytest.raises(SsoError):
            _validate_id_token_claims(
                {"exp": _exp(-3600), "aud": "client-abc"}, client_id="client-abc", issuer=None
            )

    def test_rejects_wrong_audience(self) -> None:
        with pytest.raises(SsoError):
            _validate_id_token_claims(
                {"exp": _exp(300), "aud": "someone-else"}, client_id="client-abc", issuer=None
            )

    def test_accepts_audience_list_containing_client(self) -> None:
        _validate_id_token_claims(
            {"exp": _exp(300), "aud": ["other", "client-abc"]},
            client_id="client-abc",
            issuer=None,
        )

    def test_rejects_wrong_issuer(self) -> None:
        with pytest.raises(SsoError):
            _validate_id_token_claims(
                {"exp": _exp(300), "aud": "client-abc", "iss": "https://evil"},
                client_id="client-abc",
                issuer="https://idp",
            )
