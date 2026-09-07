"""SSO connection administration + the OIDC/SAML sign-in flow.

Admin endpoints (``/sso-connections``) require an org admin session. The sign-in endpoints
(``/auth/sso/*``) are unauthenticated - they establish a session by federating an IdP
identity to a Third Brain user (creating it just-in-time on first login).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, client_ip, require_role
from app.core.security import encrypt_secret
from app.models.enums import AuditAction, OrgRole, SsoProtocol
from app.models.sso import SsoConnection
from app.schemas.auth import Tokens
from app.schemas.common import Message
from app.schemas.sso import (
    SsoCallbackRequest,
    SsoConnectionCreate,
    SsoConnectionPublic,
    SsoConnectionRead,
    SsoConnectionUpdate,
    SsoStartResponse,
)
from app.services import auth_service, sso
from app.services.metering import record_audit

router = APIRouter(tags=["sso"])

_REQUIRED_CONFIG = {
    SsoProtocol.OIDC: ("authorization_endpoint", "token_endpoint", "client_id"),
    SsoProtocol.SAML: ("idp_sso_url", "idp_x509_cert"),
}


def _validate_config(protocol: SsoProtocol, config: dict) -> None:
    missing = [k for k in _REQUIRED_CONFIG[protocol] if not (config or {}).get(k)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{protocol.value} connection requires config keys: {', '.join(missing)}",
        )


# --------------------------------------------------------------------------- #
# Admin: connection CRUD
# --------------------------------------------------------------------------- #
@router.get("/sso-connections", response_model=list[SsoConnectionRead])
async def list_connections(
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> list[SsoConnectionRead]:
    rows = (
        (
            await db.execute(
                select(SsoConnection)
                .where(SsoConnection.org_id == ctx.org_id)
                .order_by(SsoConnection.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [SsoConnectionRead.from_model(c) for c in rows]


@router.post("/sso-connections", response_model=SsoConnectionRead, status_code=201)
async def create_connection(
    payload: SsoConnectionCreate,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SsoConnectionRead:
    _validate_config(payload.protocol, payload.config)
    conn = SsoConnection(
        org_id=ctx.org_id,
        protocol=payload.protocol,
        name=payload.name,
        enabled=payload.enabled,
        email_domain=(payload.email_domain or "").lower() or None,
        config=payload.config or {},
        encrypted_secret=encrypt_secret(payload.client_secret) if payload.client_secret else None,
        default_role=payload.default_role,
    )
    db.add(conn)
    await db.flush()
    await record_audit(
        db,
        ctx,
        AuditAction.SSO_CONNECTION_UPDATED.value,
        resource_type="sso_connection",
        resource_id=conn.id,
        meta={"protocol": payload.protocol.value, "action": "create"},
    )
    await db.commit()
    await db.refresh(conn)
    return SsoConnectionRead.from_model(conn)


async def _get_conn(db: AsyncSession, ctx: AuthContext, conn_id: uuid.UUID) -> SsoConnection:
    conn = await db.get(SsoConnection, conn_id)
    if conn is None or conn.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return conn


@router.patch("/sso-connections/{conn_id}", response_model=SsoConnectionRead)
async def update_connection(
    conn_id: uuid.UUID,
    payload: SsoConnectionUpdate,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> SsoConnectionRead:
    conn = await _get_conn(db, ctx, conn_id)
    fields = payload.model_fields_set
    if payload.name is not None:
        conn.name = payload.name
    if payload.enabled is not None:
        conn.enabled = payload.enabled
    if "email_domain" in fields:
        conn.email_domain = (payload.email_domain or "").lower() or None
    if payload.config is not None:
        conn.config = payload.config
    if payload.default_role is not None:
        conn.default_role = payload.default_role
    if "client_secret" in fields:
        conn.encrypted_secret = (
            encrypt_secret(payload.client_secret) if payload.client_secret else None
        )
    _validate_config(conn.protocol, conn.config)
    await db.commit()
    await db.refresh(conn)
    return SsoConnectionRead.from_model(conn)


@router.delete("/sso-connections/{conn_id}", response_model=Message)
async def delete_connection(
    conn_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> Message:
    conn = await _get_conn(db, ctx, conn_id)
    await db.delete(conn)
    await db.commit()
    return Message(detail="SSO connection removed")


# --------------------------------------------------------------------------- #
# Sign-in flow (unauthenticated)
# --------------------------------------------------------------------------- #
@router.get("/auth/sso/available", response_model=list[SsoConnectionPublic])
async def available_connections(
    email: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[SsoConnectionPublic]:
    """Enabled connections for the login page, matched to the given email's domain.

    Only ever discloses connections for a specific email domain; without an email this
    returns nothing rather than enumerating every organization's SSO setup (name, protocol)
    to an unauthenticated caller.
    """
    if not email or "@" not in email:
        return []
    domain = email.split("@", 1)[1].lower()
    rows = (
        (
            await db.execute(
                select(SsoConnection).where(
                    SsoConnection.enabled.is_(True), SsoConnection.email_domain == domain
                )
            )
        )
        .scalars()
        .all()
    )
    return [SsoConnectionPublic(id=c.id, name=c.name, protocol=c.protocol) for c in rows]


@router.get("/auth/sso/start", response_model=SsoStartResponse)
async def sso_start(
    connection_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> SsoStartResponse:
    conn = await db.get(SsoConnection, connection_id)
    if conn is None or not conn.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    state = sso.encode_state(conn.id)
    if conn.protocol == SsoProtocol.OIDC:
        url = sso.build_authorization_url(conn, state)
    else:
        url = (conn.config or {}).get("idp_sso_url", "")
    return SsoStartResponse(url=url, state=state, protocol=conn.protocol)


async def _finish_login(db, request, conn, identity) -> Tokens:
    try:
        user = await sso.provision_sso_user(db, conn, identity)
    except sso.SsoError as exc:
        # e.g. the asserted email belongs to an existing account that is not a member of this
        # org - refuse rather than mint a session for an account this connection can't claim.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO user unresolved")
    ctx = AuthContext(org_id=conn.org_id, org_role=conn.default_role, user=user)
    await record_audit(
        db,
        ctx,
        AuditAction.USER_SSO_LOGIN.value,
        resource_type="user",
        resource_id=user.id,
        ip_address=client_ip(request),
        meta={"protocol": conn.protocol.value},
    )
    await db.commit()
    return auth_service.make_tokens(user.id, conn.org_id, user.token_version)


@router.post("/auth/sso/callback", response_model=Tokens)
async def sso_callback(
    payload: SsoCallbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """OIDC callback: exchange the code, federate the identity, return a session."""
    try:
        conn_id = sso.decode_state(payload.state)
    except sso.SsoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    conn = await db.get(SsoConnection, conn_id)
    if conn is None or not conn.enabled or conn.protocol != SsoProtocol.OIDC:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    try:
        identity = await sso.oidc_exchange(conn, payload.code)
    except sso.SsoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _finish_login(db, request, conn, identity)


@router.post("/auth/sso/saml/acs", response_model=Tokens)
async def saml_acs(
    request: Request,
    connection_id: uuid.UUID = Query(...),
    SAMLResponse: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> Tokens:
    """SAML assertion consumer: verify the signed response and return a session.

    Returns tokens as JSON for programmatic/testing use; a browser deployment would set a
    cookie or redirect with a one-time code instead.
    """
    conn = await db.get(SsoConnection, connection_id)
    if conn is None or not conn.enabled or conn.protocol != SsoProtocol.SAML:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    try:
        identity = sso.verify_saml_response(conn, SAMLResponse)
    except sso.SsoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _finish_login(db, request, conn, identity)
