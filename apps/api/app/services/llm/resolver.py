"""Provider resolution: map an ``(org, purpose)`` to concrete LLM call parameters.

The dashboard lets each organization register :class:`~app.models.connector.Connector`
rows that point at an LLM endpoint (OpenAI-compatible, Anthropic, or Google Gemini).
Exactly one *enabled* connector per ``(org, purpose)`` is marked ``is_default``; that is
the one used for embeddings / completions on behalf of the org.

:func:`resolve` returns a :class:`ResolvedProvider` for that default connector, or one of
all-``None`` fields when the org has not configured one - in which case callers fall back
to the platform defaults in :mod:`app.core.config` (and the deterministic offline ``fake``
provider when no platform key is set). This keeps every call site LLM-agnostic: they ask
the resolver for parameters and hand them straight to :mod:`app.services.llm`.
"""

from __future__ import annotations

import json
import uuid
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.models.connector import Connector
from app.models.enums import ConnectorPurpose, ConnectorType

logger = get_logger(__name__)


class ResolvedProvider(NamedTuple):
    """Concrete LLM call parameters; any field may be ``None`` to defer to platform
    defaults (``provider=None`` means "platform provider by key priority")."""

    model: str | None
    api_key: str | None
    api_base: str | None
    provider: str | None


# Connector type -> wire provider understood by :mod:`app.services.llm.providers`.
# Every OpenAI-compatible flavour speaks the "openai" wire shape.
_WIRE_PROVIDERS: dict[ConnectorType, str] = {
    ConnectorType.OPENAI: "openai",
    ConnectorType.AZURE_OPENAI: "openai",
    ConnectorType.OLLAMA: "openai",
    ConnectorType.CUSTOM: "openai",
    ConnectorType.ANTHROPIC: "anthropic",
    ConnectorType.GOOGLE: "google",
}


def wire_provider(connector_type: ConnectorType) -> str:
    """The wire provider a connector of ``connector_type`` speaks."""
    return _WIRE_PROVIDERS.get(connector_type, "openai")


# Keys under which an api base / endpoint may be stored, in priority order.
_API_BASE_KEYS = ("api_base", "base_url", "endpoint", "azure_endpoint")
# Keys under which the secret token may be stored, in priority order.
_API_KEY_KEYS = ("api_key", "key", "token", "secret_key")


def decrypt_credentials(connector: Connector) -> dict:
    """Return the connector's decrypted credential dict (``{}`` when none stored).

    Credentials are persisted as a Fernet-encrypted JSON object. For resilience a
    legacy/plain secret (non-JSON) is surfaced under the ``api_key`` key.
    """
    if not connector.encrypted_credentials:
        return {}
    try:
        raw = decrypt_secret(connector.encrypted_credentials)
    except Exception as exc:  # pragma: no cover - corrupted blob / rotated SECRET_KEY
        logger.warning("Failed to decrypt credentials for connector %s: %s", connector.id, exc)
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"api_key": raw}
    if isinstance(data, dict):
        return data
    return {"api_key": str(data)}


def api_key_of(credentials: dict) -> str | None:
    """Extract the secret token from a decrypted credential dict."""
    for key in _API_KEY_KEYS:
        val = credentials.get(key)
        if val:
            return str(val)
    return None


def base_url_from(config: dict | None, credentials: dict | None) -> str | None:
    """Resolve the provider base URL from raw ``credentials`` then non-secret ``config``.

    Works on plain dicts (not a persisted :class:`Connector`) so connector create/update can
    validate the endpoint before it is stored, using the SAME key priority the resolver reads
    at call time - the two must not drift.
    """
    config = config or {}
    credentials = credentials or {}
    for key in _API_BASE_KEYS:
        val = credentials.get(key) or config.get(key)
        if val:
            return str(val)
    return None


def api_base_of(connector: Connector, credentials: dict) -> str | None:
    """Resolve the provider base URL from credentials, then non-secret ``config``."""
    return base_url_from(connector.config, credentials)


async def default_connector(
    db: AsyncSession, org_id: uuid.UUID, purpose: ConnectorPurpose
) -> Connector | None:
    """Return the org's default, enabled connector for ``purpose`` (or ``None``)."""
    stmt = (
        select(Connector)
        .where(
            Connector.org_id == org_id,
            Connector.purpose == purpose,
            Connector.enabled.is_(True),
            Connector.is_default.is_(True),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def resolve(
    db: AsyncSession, org_id: uuid.UUID, purpose: ConnectorPurpose
) -> ResolvedProvider:
    """Resolve the LLM call parameters for an org + purpose.

    Uses the org's default enabled connector when present; otherwise returns all-``None``
    fields so the caller falls back to the platform defaults defined in
    :mod:`app.core.config`.
    """
    connector = await default_connector(db, org_id, purpose)
    if connector is None:
        return ResolvedProvider(None, None, None, None)
    credentials = decrypt_credentials(connector)
    return ResolvedProvider(
        connector.model,
        api_key_of(credentials),
        api_base_of(connector, credentials),
        wire_provider(connector.type),
    )
