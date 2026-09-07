"""Pydantic schemas for the CLI device-authorization endpoints.

The device code is a secret returned exactly once when the flow starts; the minted API
key's plaintext is returned exactly once when the approved flow is redeemed. Neither is
ever readable again.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# Scopes an admin may grant through the browser approval. Deliberately narrower than
# ``ALLOWED_SCOPES``: a terminal-initiated key never gets ``manage`` or ``*``.
DEVICE_GRANTABLE_SCOPES: frozenset[str] = frozenset({"read", "write", "search", "ingest"})
DEFAULT_DEVICE_SCOPES: tuple[str, ...] = ("search", "read", "ingest")


class DeviceAuthStart(BaseModel):
    client_name: str | None = Field(default=None, max_length=255)


class DeviceAuthStarted(BaseModel):
    """Start response - ``device_code`` is the CLI's polling secret, shown once."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceAuthTokenRequest(BaseModel):
    device_code: str = Field(min_length=1, max_length=128)


class DeviceAuthTokenResponse(BaseModel):
    """Poll response. ``api_key`` is populated exactly once, on the approved handover.

    ``org_name`` / ``acts_as_email`` identify which organization and member the key was
    bound to, so the CLI can print "Connected to <org> as <member>" and a wrong-org
    binding is immediately visible to the person who started the flow.
    """

    status: str
    api_key: str | None = None
    key_prefix: str | None = None
    scopes: list[str] | None = None
    org_name: str | None = None
    acts_as_email: str | None = None


class DeviceAuthPendingRead(ORMModel):
    """What the approval page shows an admin before they approve/deny."""

    client_name: str
    requested_scopes: list[str]
    created_at: datetime
    expires_at: datetime


class DeviceAuthApprove(BaseModel):
    user_code: str = Field(min_length=1, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scopes: list[str] | None = None
    acts_as_user_id: uuid.UUID | None = None


class DeviceAuthDeny(BaseModel):
    user_code: str = Field(min_length=1, max_length=16)
