"""Connector management - per-org LLM provider endpoints.

A connector binds an organization to an LLM provider - an OpenAI-compatible endpoint
(OpenAI, Azure OpenAI, Ollama, or any self-hosted OpenAI-compatible server), Anthropic, or
Google Gemini - for a single purpose (embedding / completion). Anthropic is completions
only (it has no embeddings API). Credentials are encrypted at rest and never returned; they
are decrypted only transiently to make a provider call. Exactly one enabled connector per
``(org, purpose)`` is the ``is_default`` used by :mod:`app.services.llm.resolver`.

Reads are available to any org member; mutations require an org admin (owner/admin).
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import AuthContext, get_auth_context, require_role
from app.core.security import encrypt_secret
from app.models.connector import Connector
from app.models.enums import ConnectorPurpose, ConnectorType, OrgRole, UsageKind
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorRead,
    ConnectorTestResult,
    ConnectorUpdate,
)
from app.services.extractors import ensure_public_url
from app.services.llm import ChatMessage, complete, embed_texts, resolver
from app.services.llm.pricing import completion_cost, embedding_cost, is_billable_provider
from app.services.metering import record_audit, record_usage

router = APIRouter(prefix="/connectors", tags=["connectors"])


async def _validate_endpoint(config: dict | None, credentials: dict | None) -> None:
    """Reject a connector whose base URL is non-http(s) or resolves to a non-public address.

    An org admin is a low-trust tenant in this multi-tenant SaaS; without this the shared
    server could be pointed at cloud metadata (169.254.169.254), loopback or private-range
    internal services and used as a blind SSRF proxy. Uses the same resolver key priority the
    call path reads, and the same is_global check as URL ingestion, so the two egress paths
    cannot drift. (An operator who genuinely runs an internal LLM should expose it publicly or
    add an egress allow-list; org-configurable private targets are the vulnerability.)
    """
    base = resolver.base_url_from(config, credentials)
    if not base:
        # No org-supplied base: native providers (OpenAI/Anthropic/Google) use their
        # platform default base URL, which needs no SSRF gating.
        return
    try:
        await ensure_public_url(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connector endpoint is not permitted: {exc}",
        ) from exc


def _validate_purpose(connector_type: ConnectorType, purpose: ConnectorPurpose) -> None:
    """Reject provider/purpose combinations the provider cannot serve."""
    if connector_type == ConnectorType.ANTHROPIC and purpose == ConnectorPurpose.EMBEDDING:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Anthropic has no embeddings API. Use an OpenAI-compatible or Google "
                "connector for embeddings."
            ),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _get_owned(db: AsyncSession, ctx: AuthContext, connector_id: uuid.UUID) -> Connector:
    """Fetch a connector, enforcing org isolation (404 otherwise)."""
    connector = await db.get(Connector, connector_id)
    if connector is None or connector.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    return connector


async def _clear_other_defaults(
    db: AsyncSession,
    org_id: uuid.UUID,
    purpose: ConnectorPurpose,
    keep_id: uuid.UUID | None = None,
) -> None:
    """Unset ``is_default`` on every other connector for the same (org, purpose)."""
    stmt = select(Connector).where(
        Connector.org_id == org_id,
        Connector.purpose == purpose,
        Connector.is_default.is_(True),
    )
    for other in (await db.execute(stmt)).scalars().all():
        if keep_id is not None and other.id == keep_id:
            continue
        other.is_default = False


def _encrypt_credentials(credentials: dict | None) -> str | None:
    """Encrypt a credential dict to a JSON blob, or ``None`` when empty."""
    if not credentials:
        return None
    return encrypt_secret(json.dumps(credentials))


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ConnectorRead])
async def list_connectors(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectorRead]:
    """List all connectors for the active organization (secrets omitted)."""
    rows = (
        (
            await db.execute(
                select(Connector)
                .where(Connector.org_id == ctx.org_id)
                .order_by(Connector.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ConnectorRead.from_model(c) for c in rows]


@router.post("", response_model=ConnectorRead, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: ConnectorCreate,
    request: Request,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ConnectorRead:
    """Create a connector. The credential map is encrypted before it touches disk.

    The connector becomes the default for its ``(org, purpose)`` when explicitly
    requested, or automatically when it is the first one for that purpose.
    """
    _validate_purpose(payload.type, payload.purpose)
    await _validate_endpoint(payload.config, payload.credentials)
    existing_default = await resolver.default_connector(db, ctx.org_id, payload.purpose)
    # A disabled connector can never be the (effective) default - the resolver only
    # returns enabled ones - so refuse to mark one default here. Doing so would clear the
    # org's real default and silently drop the provider. Mirrors ``update_connector``.
    make_default = bool(payload.enabled) and (payload.is_default or existing_default is None)

    connector = Connector(
        org_id=ctx.org_id,
        name=payload.name,
        type=payload.type,
        purpose=payload.purpose,
        model=payload.model,
        config=payload.config or {},
        encrypted_credentials=_encrypt_credentials(payload.credentials),
        is_default=make_default,
        enabled=payload.enabled,
    )
    db.add(connector)
    await db.flush()

    if make_default:
        await _clear_other_defaults(db, ctx.org_id, payload.purpose, keep_id=connector.id)

    await record_audit(
        db,
        ctx,
        "connector.created",
        resource_type="connector",
        resource_id=connector.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"type": payload.type.value, "purpose": payload.purpose.value},
    )
    await db.commit()
    await db.refresh(connector)
    return ConnectorRead.from_model(connector)


@router.get("/{connector_id}", response_model=ConnectorRead)
async def get_connector(
    connector_id: uuid.UUID,
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> ConnectorRead:
    """Fetch a single connector by id (secrets omitted)."""
    connector = await _get_owned(db, ctx, connector_id)
    return ConnectorRead.from_model(connector)


@router.patch("/{connector_id}", response_model=ConnectorRead)
async def update_connector(
    connector_id: uuid.UUID,
    payload: ConnectorUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ConnectorRead:
    """Update a connector. Provided ``credentials`` replace the stored secret;
    an empty object clears it."""
    connector = await _get_owned(db, ctx, connector_id)
    # ``model_fields_set`` tells us which fields the client actually sent, so we can
    # distinguish "omitted" from "explicitly set to a falsy value" (e.g. credentials={}).
    fields_set = payload.model_fields_set

    # Validate the effective post-update provider/purpose pair, so an unsupported
    # combination cannot be smuggled in by changing either field alone.
    _validate_purpose(
        payload.type if payload.type is not None else connector.type,
        payload.purpose if payload.purpose is not None else connector.purpose,
    )

    # Re-validate the endpoint whenever the config or credentials (where a base URL may live)
    # change, using the effective post-update values so a private/internal target cannot be
    # smuggled in via an update either.
    if payload.config is not None or "credentials" in fields_set:
        effective_config = payload.config if payload.config is not None else connector.config
        effective_creds = (
            payload.credentials
            if "credentials" in fields_set
            else resolver.decrypt_credentials(connector)
        )
        await _validate_endpoint(effective_config, effective_creds)

    if payload.name is not None:
        connector.name = payload.name
    if payload.type is not None:
        connector.type = payload.type
    if payload.purpose is not None:
        connector.purpose = payload.purpose
    if payload.model is not None:
        connector.model = payload.model
    if payload.config is not None:
        connector.config = payload.config
    if payload.enabled is not None:
        connector.enabled = payload.enabled
    # ``credentials`` present (even as {}) means "replace"; {} clears the secret.
    if "credentials" in fields_set:
        connector.encrypted_credentials = _encrypt_credentials(payload.credentials)

    # A disabled connector can never remain a default.
    if not connector.enabled:
        connector.is_default = False
    elif payload.is_default is True:
        connector.is_default = True
    elif payload.is_default is False:
        connector.is_default = False

    # Preserve the "exactly one default per (org, purpose)" invariant, even when the
    # purpose changed while the connector stayed the default (which would otherwise
    # leave two defaults for the new purpose).
    if connector.enabled and connector.is_default:
        await _clear_other_defaults(db, ctx.org_id, connector.purpose, keep_id=connector.id)

    await record_audit(
        db,
        ctx,
        "connector.updated",
        resource_type="connector",
        resource_id=connector.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"fields": sorted(fields_set)},
    )
    await db.commit()
    await db.refresh(connector)
    return ConnectorRead.from_model(connector)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a connector. If it was the default, promote another enabled one."""
    connector = await _get_owned(db, ctx, connector_id)
    was_default = connector.is_default
    purpose = connector.purpose

    await db.delete(connector)
    await db.flush()

    if was_default:
        replacement = (
            (
                await db.execute(
                    select(Connector)
                    .where(
                        Connector.org_id == ctx.org_id,
                        Connector.purpose == purpose,
                        Connector.enabled.is_(True),
                    )
                    .order_by(Connector.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if replacement is not None:
            replacement.is_default = True

    await record_audit(
        db,
        ctx,
        "connector.deleted",
        resource_type="connector",
        resource_id=connector_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# Test - a tiny live round-trip against the configured provider.
# --------------------------------------------------------------------------- #
@router.post("/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector(
    connector_id: uuid.UUID,
    ctx: AuthContext = Depends(require_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> ConnectorTestResult:
    """Exercise the connector with a minimal embed/completion and report ok + latency.

    The LLM facade falls back to a deterministic offline provider on failure, so when a
    real credential is configured but the live call falls back to ``fake`` we treat the
    test as failed and say so.
    """
    connector = await _get_owned(db, ctx, connector_id)
    # Legacy rows predating the purpose gate could still hold an unsupported pair;
    # surface the same clear error the create/update path gives.
    _validate_purpose(connector.type, connector.purpose)
    credentials = resolver.decrypt_credentials(connector)
    api_key = resolver.api_key_of(credentials)
    api_base = resolver.api_base_of(connector, credentials)
    provider_kind = resolver.wire_provider(connector.type)

    started = time.perf_counter()
    provider = ""
    tokens_in = tokens_out = 0
    kind = UsageKind.EMBEDDING
    ok = False
    message = ""
    dim_mismatch = ""
    try:
        if connector.purpose == ConnectorPurpose.EMBEDDING:
            kind = UsageKind.EMBEDDING
            result = await embed_texts(
                ["Third Brain connector health check."],
                connector.model,
                api_key=api_key,
                api_base=api_base,
                provider=provider_kind,
            )
            provider = result.provider
            tokens_in = result.tokens
            produced = bool(result.vectors and result.vectors[0])
            # The document_chunks.embedding column is a fixed-width vector; a connector whose
            # embeddings have a different dimension would pass this test but then break every
            # ingestion and search, so reject a dimension mismatch here.
            if produced and len(result.vectors[0]) != settings.EMBEDDING_DIM:
                dim_mismatch = (
                    f"Embedding dimension {len(result.vectors[0])} does not match the "
                    f"configured {settings.EMBEDDING_DIM}."
                )
        else:
            # Completion connectors validate with a tiny completion call.
            kind = UsageKind.COMPLETION
            result = await complete(
                [ChatMessage(role="user", content="Reply with 'ok'.")],
                connector.model,
                api_key=api_key,
                api_base=api_base,
                provider=provider_kind,
                # On the Claude 5 family ``max_tokens`` caps default-on thinking PLUS
                # the visible answer; a tiny cap yields an empty reply.
                max_tokens=256,
            )
            provider = result.provider
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            produced = bool(result.text and result.text.strip())

        # The offline stub labels itself "offline" (never "fake"). If a real credential is
        # configured but the call still fell back offline, the live call failed.
        used_offline = not is_billable_provider(provider)
        if not produced:
            ok, message = False, "Provider returned no output."
        elif dim_mismatch:
            ok, message = False, dim_mismatch
        elif used_offline and api_key:
            ok = False
            message = (
                "The live provider call did not succeed (offline fallback used). "
                "Verify the credentials, model name and endpoint."
            )
        else:
            ok = True
            message = (
                f"{connector.purpose.value.title()} test succeeded via the "
                f"'{provider}' provider using model '{connector.model}'."
            )
    except Exception as exc:  # pragma: no cover - defensive; facade rarely raises
        ok = False
        message = f"Provider call failed: {exc}"

    latency_ms = int((time.perf_counter() - started) * 1000)

    if not is_billable_provider(provider):
        cost = 0.0
    elif kind == UsageKind.EMBEDDING:
        cost = embedding_cost(connector.model, tokens_in)
    else:
        cost = completion_cost(connector.model, tokens_in, tokens_out)
    await record_usage(
        db,
        ctx,
        kind,
        provider=provider or None,
        model=connector.model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        units=1,
        cost_usd=cost,
        latency_ms=latency_ms,
        meta={"connector_test": True, "connector_id": str(connector.id), "ok": ok},
    )
    await db.commit()

    return ConnectorTestResult(ok=ok, message=message, latency_ms=latency_ms)
