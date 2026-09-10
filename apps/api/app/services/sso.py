"""SSO: OIDC + SAML 2.0 sign-in with just-in-time provisioning.

Both protocols resolve an IdP subject + email, then map to a Third Brain user via
:class:`FederatedIdentity` (creating the user + membership on first login). SAML responses
are signature-verified with :mod:`signxml` against the connection's IdP certificate - no
system ``xmlsec`` dependency, so it is fully testable against a local mock IdP.

Security note: the OIDC ``id_token`` is decoded for its claims here; a production
deployment should additionally verify its signature against the IdP JWKS. SAML assertions
ARE signature-verified.
"""

from __future__ import annotations

import base64
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from lxml import etree
from signxml import XMLVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.models.enums import MembershipStatus
from app.models.sso import FederatedIdentity, SsoConnection
from app.models.user import Membership, User
from app.services.identity import get_membership, get_user_by_email, provision_user

logger = get_logger(__name__)

_SAML_ASSERTION_NS = "urn:oasis:names:tc:SAML:2.0:assertion"

_SAML_PARSER = etree.XMLParser(
    resolve_entities=False,
    load_dtd=False,
    no_network=True,
    huge_tree=False,
)
"""Hardened parser for the SAMLResponse.

The assertion's signature is verified immediately after parsing, but that cannot protect
this step: parsing happens first, and the ACS endpoint is unauthenticated by design (it
has to be - the IdP posts to it), so these bytes are attacker-controlled. Current lxml
already declines external entities by default; stating it explicitly means the guarantee
does not rest on a library default that could differ across versions or environments.
Real SAML carries no entities or DTD, so nothing legitimate is lost.
"""
_STATE_TTL_SECONDS = 600

_SAML_CLOCK_SKEW_SECONDS = 180
"""Tolerate modest IdP/SP clock drift when checking an assertion's validity window."""


_OIDC_LEEWAY_SECONDS = 120


@dataclass(frozen=True)
class SsoIdentity:
    subject: str
    email: str
    name: str | None = None


class SsoError(Exception):
    """SSO could not be completed (bad state, signature, or missing email)."""


def _provider_key(connection: SsoConnection) -> str:
    return f"{connection.protocol.value}:{connection.id}"


def encode_state(connection_id: uuid.UUID, next_path: str | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "cid": str(connection_id),
        "iat": now,
        "exp": now + timedelta(seconds=_STATE_TTL_SECONDS),
        "typ": "sso_state",
    }
    if next_path:
        payload["next"] = next_path
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_state(state: str) -> uuid.UUID:
    try:
        claims = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise SsoError("Invalid or expired SSO state") from exc
    if claims.get("typ") != "sso_state":
        raise SsoError("Invalid SSO state")
    return uuid.UUID(claims["cid"])


def build_authorization_url(connection: SsoConnection, state: str) -> str:
    """Build the OIDC authorization-endpoint redirect URL."""
    cfg = connection.config or {}
    params = {
        "response_type": "code",
        "client_id": cfg.get("client_id", ""),
        "redirect_uri": cfg.get("redirect_uri", f"{settings.APP_BASE_URL}/sso/callback"),
        "scope": cfg.get("scope", "openid email profile"),
        "state": state,
    }
    return f"{cfg.get('authorization_endpoint', '')}?{urllib.parse.urlencode(params)}"


def _validate_id_token_claims(claims: dict, *, client_id: str, issuer: str | None) -> None:
    """Enforce the security-critical id_token claims (``exp``, ``aud``, ``iss``).

    Pure and side-effect-free so it is unit-testable without a live IdP. ``exp`` must be
    present and unexpired (small leeway); when the connection's ``client_id`` / ``issuer``
    are known, the token's ``aud`` must include the client_id and its ``iss`` must match -
    so a token minted for another relying party (or a stale one) is rejected.
    """
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise SsoError("IdP id_token has no expiry (exp)")
    now = int(datetime.now(UTC).timestamp())
    if now > int(exp) + _OIDC_LEEWAY_SECONDS:
        raise SsoError("IdP id_token has expired")
    if client_id:
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if client_id not in audiences:
            raise SsoError("IdP id_token audience does not match this connection's client_id")
    if issuer and claims.get("iss") != issuer:
        raise SsoError("IdP id_token issuer does not match the configured issuer")


def _decode_id_token(cfg: dict, id_token: str) -> dict:
    """Decode and validate an OIDC ``id_token`` before its identity is trusted.

    When the connection configures a ``jwks_uri`` the token's SIGNATURE is verified against
    the IdP's published keys (the correct production posture). Otherwise the token - which
    was just fetched directly from the IdP token endpoint over TLS - is decoded without a
    signature check, but its ``exp``/``aud``/``iss`` claims are still enforced. Either way
    the claims are validated before use, closing the previous "decode, trust blindly" gap.
    """
    client_id = cfg.get("client_id") or ""
    issuer = cfg.get("issuer") or None
    jwks_uri = cfg.get("jwks_uri")
    if jwks_uri:  # pragma: no cover - requires a live IdP JWKS endpoint
        try:
            signing_key = jwt.PyJWKClient(jwks_uri).get_signing_key_from_jwt(id_token)
            return jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
                audience=client_id or None,
                issuer=issuer,
                leeway=_OIDC_LEEWAY_SECONDS,
                options={"verify_aud": bool(client_id), "verify_iss": bool(issuer)},
            )
        except jwt.PyJWTError as exc:
            raise SsoError(f"IdP id_token signature verification failed: {exc}") from exc
    claims = jwt.decode(id_token, options={"verify_signature": False})
    logger.warning("oidc_id_token_signature_unverified", reason="no jwks_uri configured")
    _validate_id_token_claims(claims, client_id=client_id, issuer=issuer)
    return claims


async def oidc_exchange(connection: SsoConnection, code: str) -> SsoIdentity:
    """Exchange an OIDC authorization code for the caller's identity."""
    cfg = connection.config or {}
    secret = decrypt_secret(connection.encrypted_secret) if connection.encrypted_secret else None
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.get("redirect_uri", f"{settings.APP_BASE_URL}/sso/callback"),
        "client_id": cfg.get("client_id", ""),
    }
    if secret:
        data["client_secret"] = secret
    async with httpx.AsyncClient(timeout=15.0) as client:  # pragma: no cover - needs live IdP
        resp = await client.post(cfg.get("token_endpoint", ""), data=data)
        resp.raise_for_status()
        token = resp.json()
    id_token = token.get("id_token")
    if not id_token:
        raise SsoError("IdP returned no id_token")
    claims = _decode_id_token(cfg, id_token)
    email = claims.get("email")
    if not email:
        raise SsoError("IdP id_token carried no email")
    return SsoIdentity(subject=claims.get("sub") or email, email=email, name=claims.get("name"))


def _parse_saml_instant(value: str) -> datetime:
    """Parse a SAML/XSD ``dateTime`` (UTC, usually ``Z``-suffixed) to an aware datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _enforce_assertion_conditions(connection: SsoConnection, verified) -> None:
    """Reject an assertion outside its validity window (or intended audience).

    Signature verification alone leaves a captured, validly-signed SAMLResponse replayable
    indefinitely. SAML bounds each assertion with ``Conditions/@NotBefore`` and
    ``@NotOnOrAfter`` (a short window); enforcing them - read from the SIGNED subtree -
    limits any replay to that window. When the connection configures an expected ``audience``
    (this SP's entityID) the assertion's ``AudienceRestriction`` must include it, so a token
    minted for a different service provider is rejected. A small clock skew is tolerated.
    """
    now = datetime.now(UTC)
    skew = timedelta(seconds=_SAML_CLOCK_SKEW_SECONDS)
    conditions = verified.find(f".//{{{_SAML_ASSERTION_NS}}}Conditions")
    if conditions is None:
        raise SsoError("SAML assertion has no Conditions to bound its validity")

    raw_not_after = conditions.get("NotOnOrAfter")
    if not raw_not_after:
        raise SsoError("SAML assertion Conditions has no NotOnOrAfter (unbounded validity)")
    raw_not_before = conditions.get("NotBefore")
    try:
        if raw_not_before and now + skew < _parse_saml_instant(raw_not_before):
            raise SsoError("SAML assertion is not yet valid")
        if now - skew >= _parse_saml_instant(raw_not_after):
            raise SsoError("SAML assertion has expired")
    except ValueError as exc:
        raise SsoError("SAML assertion has an invalid Conditions timestamp") from exc

    expected_audience = (connection.config or {}).get("audience") or (connection.config or {}).get(
        "sp_entity_id"
    )
    if expected_audience:
        audiences = {
            (el.text or "").strip()
            for el in verified.findall(f".//{{{_SAML_ASSERTION_NS}}}Audience")
        }
        if expected_audience not in audiences:
            raise SsoError("SAML assertion audience does not match this service provider")


def verify_saml_response(connection: SsoConnection, saml_response_b64: str) -> SsoIdentity:
    """Verify a base64 SAMLResponse's signature and extract the subject/email.

    Verification failure is caught broadly because signxml raises a variety of validation
    errors. Once the signature is valid the assertion is bounded in time (and audience) so a
    captured, validly-signed response cannot be replayed indefinitely.
    """
    cfg = connection.config or {}
    cert = cfg.get("idp_x509_cert")
    if not cert:
        raise SsoError("SAML connection has no IdP certificate configured")
    try:
        xml_bytes = base64.b64decode(saml_response_b64)
        doc = etree.fromstring(xml_bytes, parser=_SAML_PARSER)
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SsoError("Malformed SAML response") from exc
    try:
        verified = XMLVerifier().verify(doc, x509_cert=cert).signed_xml
    except Exception as exc:
        raise SsoError(f"SAML signature verification failed: {exc}") from exc

    _enforce_assertion_conditions(connection, verified)

    name_id = verified.find(f".//{{{_SAML_ASSERTION_NS}}}NameID")
    if name_id is None or not (name_id.text or "").strip():
        raise SsoError("SAML assertion carried no NameID")
    subject = name_id.text.strip()
    email = subject
    for attr in verified.findall(f".//{{{_SAML_ASSERTION_NS}}}Attribute"):
        if (attr.get("Name") or "").lower() in ("email", "mail", "emailaddress"):
            value = attr.find(f"{{{_SAML_ASSERTION_NS}}}AttributeValue")
            if value is not None and value.text:
                email = value.text.strip()
    return SsoIdentity(subject=subject, email=email)


async def _has_other_org_membership(
    db: AsyncSession, user_id: uuid.UUID, this_org_id: uuid.UUID
) -> bool:
    """True when ``user_id`` holds a membership (any status) in an org other than ``this_org_id``.

    Signals that the account is independently owned by another tenant, so this org's IdP must
    not be allowed to claim it. Mirrors the cross-tenant check in reset_member_password.
    """
    row = (
        await db.execute(
            select(Membership.id)
            .where(Membership.user_id == user_id, Membership.org_id != this_org_id)
            .limit(1)
        )
    ).first()
    return row is not None


async def provision_sso_user(db: AsyncSession, connection: SsoConnection, identity: SsoIdentity):
    """Map an IdP identity to a Third Brain user, creating it on first login.

    An already-federated subject respects an admin's offboarding: a previously-federated
    user whose membership was later suspended must not be able to SSO back in.

    On the first federation for an IdP subject the user is just-in-time provisioned. But
    NEVER adopt a pre-existing account this org does not EXCLUSIVELY own. An org supplies
    its own IdP and signing certificate, so without this guard a malicious org could sign an
    assertion claiming someone else's email and have the ACS/callback mint a session for
    that existing account - a full account takeover (and, via /orgs/switch, a pivot into the
    victim's other orgs). Org membership is NOT proof of ownership: an admin can create an
    INVITED membership for any existing email WITHOUT the target's consent (see
    ``routes/orgs.invite_member``), so "is a member" is attacker-controllable. We therefore
    adopt only an account this org exclusively owns - one with no self-set password AND no
    membership in any OTHER org - and provision a brand-new email fresh. An
    independently-owned account (self-registered, or a member of another tenant) must sign in
    with its password or an explicit, authenticated account-link, never via this org's
    unverified IdP. Mirrors ``reset_member_password``'s cross-tenant guard. (``email_domain``
    is not a boundary here: it is unverified/attacker-set.)

    A first SSO login must never silently reactivate a suspended (offboarded) member, so a
    SUSPENDED membership is refused; a newly-created or still-INVITED membership is activated
    instead.
    """
    provider = _provider_key(connection)
    fed = (
        await db.execute(
            select(FederatedIdentity).where(
                FederatedIdentity.provider == provider,
                FederatedIdentity.subject == identity.subject,
            )
        )
    ).scalar_one_or_none()
    if fed is not None:
        user = await db.get(User, fed.user_id)
        if user is not None:
            membership = await get_membership(db, connection.org_id, user.id)
            if membership is not None and membership.status == MembershipStatus.SUSPENDED:
                raise SsoError("Your membership of this organization is suspended.")
            user.last_login_at = datetime.now(UTC)
        return user
    existing = await get_user_by_email(db, identity.email)
    if existing is not None:
        membership = await get_membership(db, connection.org_id, existing.id)
        if membership is None:
            raise SsoError(
                "This email already has a Third Brain account that is not a member of this "
                "organization; it cannot be claimed via SSO."
            )
        if membership.status == MembershipStatus.SUSPENDED:
            raise SsoError("Your membership of this organization is suspended.")
        if existing.hashed_password is not None or await _has_other_org_membership(
            db, existing.id, connection.org_id
        ):
            raise SsoError(
                "This email is already registered independently (it has its own password or "
                "belongs to another organization) and cannot be claimed via this "
                "organization's SSO; sign in with your password instead."
            )
    user, membership, _ = await provision_user(
        db,
        connection.org_id,
        email=identity.email,
        full_name=identity.name,
        role=connection.default_role,
        status=MembershipStatus.ACTIVE,
    )
    if membership.status != MembershipStatus.SUSPENDED:
        membership.status = MembershipStatus.ACTIVE
    user.last_login_at = datetime.now(UTC)
    db.add(
        FederatedIdentity(
            org_id=connection.org_id,
            user_id=user.id,
            connection_id=connection.id,
            provider=provider,
            subject=identity.subject,
        )
    )
    return user
