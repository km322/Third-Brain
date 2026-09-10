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


class TestSamlParserHardening:
    """The SAMLResponse parser must never resolve entities or fetch a DTD.

    The ACS endpoint is unauthenticated by design - the IdP POSTs to it - so these bytes
    are attacker-controlled, and parsing happens BEFORE the signature is verified. Signature
    checking therefore cannot protect this step, which is what makes the parser
    configuration itself load-bearing.
    """

    _XXE = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE r [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
        b"<r>&xxe;</r>"
    )

    def test_external_entity_is_never_expanded(self) -> None:
        """Refusing outright or parsing it unexpanded are both fine; leaking file content
        is not. Asserted as a property so a future lxml default cannot reintroduce the read.
        """
        from lxml import etree

        from app.services.sso import _SAML_PARSER

        try:
            doc = etree.fromstring(self._XXE, parser=_SAML_PARSER)
        except etree.XMLSyntaxError:
            return
        assert "root:" not in (doc.text or ""), "external entity was expanded"

    def test_internal_entities_are_not_expanded(self) -> None:
        """``resolve_entities=False`` is not readable off the parser object, so assert the
        behaviour it buys: an internal entity - the billion-laughs primitive - stays
        unexpanded. Behavioural, so it holds even if the flag is renamed.
        """
        from lxml import etree

        from app.services.sso import _SAML_PARSER

        doc = etree.fromstring(
            b'<?xml version="1.0"?>\n<!DOCTYPE r [ <!ENTITY lol "aaaaaaaaaa"> ]>\n<r>&lol;</r>',
            parser=_SAML_PARSER,
        )
        assert "aaaaaaaaaa" not in (doc.text or "")

    def test_malformed_saml_is_rejected_cleanly(self) -> None:
        """Undecodable XML surfaces as SsoError, not an lxml exception escaping the route."""
        from app.models.sso import SsoConnection
        from app.services.sso import SsoError, verify_saml_response

        conn = SsoConnection(config={"idp_x509_cert": "not-a-cert"})
        with pytest.raises(SsoError):
            verify_saml_response(conn, "bm90LXhtbA==")
