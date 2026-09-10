"""Integration: SSO connection admin + OIDC and SAML sign-in with JIT provisioning."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import factories
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner
from sqlalchemy import func, select

from app.models.sso import FederatedIdentity
from app.services import sso as sso_service
from app.services.identity import get_membership, get_user_by_email

pytestmark = pytest.mark.integration

_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"


async def _oidc_connection(client, api, headers) -> str:
    resp = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "oidc",
            "name": "Okta",
            "email_domain": "corp.com",
            "config": {
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "client_id": "client-abc",
                "redirect_uri": "http://localhost:3000/sso/callback",
            },
            "client_secret": "s3cr3t",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["has_secret"] is True
    return resp.json()["id"]


async def test_oidc_login_provisions_and_is_idempotent(
    client, db_session, token_headers, api, monkeypatch
) -> None:
    """The login page discovers the connection by email domain, and the first callback JIT
    provisions the user; a second login with the same IdP subject reuses that user (no
    duplicate identity/user).
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    conn_id = await _oidc_connection(client, api, headers)

    avail = await client.get(f"{api}/auth/sso/available", params={"email": "jo@corp.com"})
    assert any(c["id"] == conn_id for c in avail.json())

    start = await client.get(f"{api}/auth/sso/start", params={"connection_id": conn_id})
    assert start.status_code == 200, start.text
    assert start.json()["url"].startswith("https://idp.example.com/authorize?")
    state = start.json()["state"]

    async def fake_exchange(conn, code):
        return sso_service.SsoIdentity(
            subject="idp-user-1", email="sso.user@corp.com", name="SSO User"
        )

    monkeypatch.setattr(sso_service, "oidc_exchange", fake_exchange)

    cb = await client.post(f"{api}/auth/sso/callback", json={"code": "abc", "state": state})
    assert cb.status_code == 200, cb.text
    assert cb.json()["access_token"]

    user = await get_user_by_email(db_session, "sso.user@corp.com")
    assert user is not None
    membership = await get_membership(db_session, org.id, user.id)
    assert membership is not None

    state2 = sso_service.encode_state(uuid.UUID(conn_id))
    cb2 = await client.post(f"{api}/auth/sso/callback", json={"code": "abc2", "state": state2})
    assert cb2.status_code == 200, cb2.text
    fed_count = (
        await db_session.execute(
            select(func.count())
            .select_from(FederatedIdentity)
            .where(FederatedIdentity.org_id == org.id)
        )
    ).scalar_one()
    assert fed_count == 1


def _self_signed() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, cert_pem


def _signed_saml_response(
    key_pem: str, cert_pem: str, email: str, *, not_on_or_after: datetime | None = None
) -> str:
    """A base64 SAMLResponse signed with ``key_pem``/``cert_pem``, asserting ``email``.

    Real IdPs bound every assertion in time, and the ACS now enforces this window, so the
    assertion carries Conditions; ``not_on_or_after`` lets a caller push it into the past.
    """
    nsmap = {"samlp": _SAMLP_NS, "saml": _SAML_NS}
    response = etree.Element(f"{{{_SAMLP_NS}}}Response", nsmap=nsmap, ID="_resp1", Version="2.0")
    assertion = etree.SubElement(response, f"{{{_SAML_NS}}}Assertion", ID="_assert1", Version="2.0")
    subject = etree.SubElement(assertion, f"{{{_SAML_NS}}}Subject")
    name_id = etree.SubElement(subject, f"{{{_SAML_NS}}}NameID")
    name_id.text = email
    now = datetime.now(UTC)
    conditions = etree.SubElement(assertion, f"{{{_SAML_NS}}}Conditions")
    conditions.set("NotBefore", (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    conditions.set(
        "NotOnOrAfter",
        (not_on_or_after or now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    signed = XMLSigner().sign(response, key=key_pem, cert=cert_pem)
    return base64.b64encode(etree.tostring(signed)).decode()


async def test_saml_acs_verifies_signature_and_provisions(
    client, db_session, token_headers, api
) -> None:
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    key_pem, cert_pem = _self_signed()

    created = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "saml",
            "name": "Corp SAML",
            "config": {
                "idp_sso_url": "https://idp.example.com/sso",
                "idp_x509_cert": cert_pem,
            },
        },
    )
    assert created.status_code == 201, created.text
    conn_id = created.json()["id"]

    saml_response = _signed_saml_response(key_pem, cert_pem, "saml.user@corp.com")
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 200, acs.text
    assert acs.json()["access_token"]
    assert await get_user_by_email(db_session, "saml.user@corp.com") is not None


async def test_saml_acs_rejects_tampered_response(client, db_session, token_headers, api) -> None:
    """A DIFFERENT cert is configured on the connection, so the (validly-signed) response
    fails verification and no user is provisioned.
    """
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    key_pem, cert_pem = _self_signed()
    _, other_cert = _self_signed()
    created = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "saml",
            "name": "Corp SAML",
            "config": {"idp_sso_url": "https://idp/sso", "idp_x509_cert": other_cert},
        },
    )
    conn_id = created.json()["id"]
    saml_response = _signed_saml_response(key_pem, cert_pem, "evil@corp.com")
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 400, acs.text
    assert await get_user_by_email(db_session, "evil@corp.com") is None


async def test_saml_acs_rejects_expired_assertion(client, db_session, token_headers, api) -> None:
    """A validly-signed but EXPIRED assertion is rejected, so a captured SAMLResponse cannot
    be replayed past its short validity window. The assertion below is signed correctly, but
    its NotOnOrAfter is well in the past."""
    org, owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(owner.id, org.id)
    key_pem, cert_pem = _self_signed()
    created = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "saml",
            "name": "Corp SAML",
            "config": {"idp_sso_url": "https://idp.example.com/sso", "idp_x509_cert": cert_pem},
        },
    )
    conn_id = created.json()["id"]
    expired = datetime.now(UTC) - timedelta(minutes=10)
    saml_response = _signed_saml_response(
        key_pem, cert_pem, "late@corp.com", not_on_or_after=expired
    )
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 400, acs.text
    assert await get_user_by_email(db_session, "late@corp.com") is None


async def test_saml_acs_cannot_hijack_existing_user(client, db_session, token_headers, api) -> None:
    """A malicious org must not mint a session for an existing user by asserting their email.

    The attacker owns a separate org, configures a SAML connection with a cert they control,
    and signs an assertion claiming a victim's email. The ACS must refuse rather than adopt
    the victim's pre-existing account - otherwise anyone who can self-register an org could
    take over any account, and the victim must not be dragged into the attacker's org.
    """
    victim_org, victim, _ = await factories.create_org_with_owner(db_session)
    evil_org, evil_owner, _ = await factories.create_org_with_owner(db_session)
    headers = token_headers(evil_owner.id, evil_org.id)
    key_pem, cert_pem = _self_signed()
    created = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "saml",
            "name": "Evil SAML",
            "config": {"idp_sso_url": "https://idp/sso", "idp_x509_cert": cert_pem},
        },
    )
    conn_id = created.json()["id"]

    saml_response = _signed_saml_response(key_pem, cert_pem, victim.email)
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 403, acs.text
    assert await get_membership(db_session, evil_org.id, victim.id) is None


async def test_saml_acs_cannot_hijack_invited_existing_user(
    client, db_session, token_headers, api
) -> None:
    """The invite-first takeover variant: an admin-created INVITED membership is NOT consent.

    An attacker owns a separate org, INVITES a victim's existing email (creating an INVITED
    membership without the victim's consent), then signs an assertion for that email with a
    cert they control. Because the victim's account is independently owned (it has its own
    password / another org), the ACS must still refuse - membership alone is not proof of
    ownership - rather than adopt+activate the account and mint a session for the victim. The
    seeded membership must be left un-activated and no federated identity linked.
    """
    from app.models.enums import MembershipStatus

    _victim_org, victim, _ = await factories.create_org_with_owner(db_session)
    evil_org, evil_owner, _ = await factories.create_org_with_owner(db_session)
    evil_headers = token_headers(evil_owner.id, evil_org.id)

    invited = await client.post(
        f"{api}/orgs/members/invite",
        headers=evil_headers,
        json={"email": victim.email, "role": "viewer"},
    )
    assert invited.status_code == 201, invited.text

    key_pem, cert_pem = _self_signed()
    created = await client.post(
        f"{api}/sso-connections",
        headers=evil_headers,
        json={
            "protocol": "saml",
            "name": "Evil SAML",
            "config": {"idp_sso_url": "https://idp/sso", "idp_x509_cert": cert_pem},
        },
    )
    conn_id = created.json()["id"]
    saml_response = _signed_saml_response(key_pem, cert_pem, victim.email)
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 403, acs.text
    membership = await get_membership(db_session, evil_org.id, victim.id)
    assert membership is not None and membership.status == MembershipStatus.INVITED
    linked = (
        await db_session.execute(
            select(func.count())
            .select_from(FederatedIdentity)
            .where(FederatedIdentity.user_id == victim.id)
        )
    ).scalar_one()
    assert linked == 0


async def test_saml_acs_refuses_suspended_member(client, db_session, token_headers, api) -> None:
    """A suspended member must not regain access - or be silently reactivated - via SSO."""
    from app.models.enums import MembershipStatus

    org, owner, _ = await factories.create_org_with_owner(db_session)
    member, membership = await factories.add_member(db_session, org=org, email="suspended@corp.com")
    membership.status = MembershipStatus.SUSPENDED
    await db_session.commit()

    headers = token_headers(owner.id, org.id)
    key_pem, cert_pem = _self_signed()
    created = await client.post(
        f"{api}/sso-connections",
        headers=headers,
        json={
            "protocol": "saml",
            "name": "Corp SAML",
            "config": {"idp_sso_url": "https://idp/sso", "idp_x509_cert": cert_pem},
        },
    )
    conn_id = created.json()["id"]
    saml_response = _signed_saml_response(key_pem, cert_pem, member.email)
    acs = await client.post(
        f"{api}/auth/sso/saml/acs",
        params={"connection_id": conn_id},
        data={"SAMLResponse": saml_response},
    )
    assert acs.status_code == 403, acs.text
    await db_session.refresh(membership)
    assert membership.status == MembershipStatus.SUSPENDED
