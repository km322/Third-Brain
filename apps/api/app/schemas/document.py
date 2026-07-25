"""Request/response schemas for the documents API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.core.config import settings
from app.models.enums import (
    DocumentStatus,
    PermissionLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
    Visibility,
)
from app.schemas.common import ORMModel

# Cap inline text so a single request cannot buffer an unbounded body in memory. Mirrors
# the 25 MB upload cap (characters, not bytes, but the same order of magnitude).
MAX_TEXT_CONTENT_CHARS = 25 * 1024 * 1024


class DocumentTextCreate(BaseModel):
    """Create a document from an inline text/markdown body."""

    collection_id: uuid.UUID
    title: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=MAX_TEXT_CONTENT_CHARS)
    visibility: Visibility | None = None


class DocumentUrlCreate(BaseModel):
    """Create a document by fetching a remote URL."""

    collection_id: uuid.UUID
    url: HttpUrl
    title: str | None = Field(default=None, max_length=1024)
    visibility: Visibility | None = None


class DocumentItem(ORMModel):
    """A document row as surfaced in lists and detail views."""

    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    source_type: SourceType
    source_uri: str | None = None
    mime_type: str | None = None
    status: DocumentStatus
    visibility: Visibility | None = None
    chunk_count: int
    size_bytes: int
    error: str | None = None
    indexed_at: datetime | None = None
    created_at: datetime
    # Trust + governance signals (features: verification, DLP).
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    sensitivity: SensitivityLevel = SensitivityLevel.NONE
    # Provenance marker read from the document's ``meta`` blob. ``"mcp"`` means an agent
    # wrote it through the MCP ``add_knowledge`` tool; ``None`` for dashboard/REST-created
    # documents. Only the marker is surfaced, never the rest of ``meta``.
    via: str | None = None
    # Capture category read from ``meta`` (e.g. ``"decision"``), set by the MCP
    # ``add_knowledge`` tool's ``doc_type``; ``None`` for uncategorized documents.
    doc_type: str | None = None
    # Absolute capability URL for an image document's original bytes (derived from the
    # ``file_token`` stamped into meta at ingestion); None for non-image documents.
    image_url: str | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _derive_via(cls, data: Any, handler: Any) -> DocumentItem:
        item = handler(data)
        meta = data.get("meta") if isinstance(data, dict) else getattr(data, "meta", None)
        meta = meta if isinstance(meta, dict) else {}
        if item.via is None:
            marker = meta.get("via")
            item.via = marker if isinstance(marker, str) else None
        if item.doc_type is None:
            dt = meta.get("doc_type")
            item.doc_type = dt if isinstance(dt, str) else None
        token = meta.get("file_token")
        if item.image_url is None and isinstance(token, str) and token:
            base = settings.PUBLIC_API_URL.rstrip("/")
            item.image_url = f"{base}{settings.API_V1_PREFIX}/files/{token}"
        return item


class DocumentVerify(BaseModel):
    """Body for ``POST /documents/{id}/verify``."""

    review_interval_days: int | None = Field(
        default=None,
        ge=0,
        description="Days until the document needs re-review (0 = never expires; "
        "omit for the org default).",
    )


class DocumentContent(BaseModel):
    """A document's full source text, as loaded from blob storage for the editor."""

    id: uuid.UUID
    title: str
    content: str
    mime_type: str | None = None
    source_type: SourceType
    # Whether THIS caller may save edits (effective permission >= editor and a text source).
    # The dashboard gates its editor on this, not on org role.
    editable: bool
    permission: PermissionLevel
    chunk_count: int


class DocumentContentUpdate(BaseModel):
    """Replacement source text for a document; saving re-chunks and re-embeds inline."""

    content: str = Field(min_length=1, max_length=MAX_TEXT_CONTENT_CHARS)


class DocumentChunkRead(ORMModel):
    """A single indexed chunk of a document."""

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    # Read from the ORM ``meta`` attribute; serialized to clients as ``metadata``.
    meta: dict = Field(default_factory=dict, serialization_alias="metadata")


class SecretScanSample(BaseModel):
    """One redacted excerpt of a detected secret (never the raw value)."""

    redacted: str
    line: int


class SecretScanFinding(BaseModel):
    """One detector's findings within a quarantined document, with redacted samples."""

    detector: str
    label: str
    severity: str
    occurrences: int
    samples: list[SecretScanSample] = Field(default_factory=list)


class QuarantineDocument(BaseModel):
    """The quarantined document's identity, as shown on the review screen."""

    id: uuid.UUID
    title: str
    status: DocumentStatus
    source_type: SourceType
    mime_type: str | None = None
    size_bytes: int
    created_at: datetime
    # The document's own visibility override (NULL means it inherits the collection's).
    visibility: Visibility | None = None


class QuarantineCollection(BaseModel):
    """Where the document will land if approved."""

    id: uuid.UUID
    name: str
    visibility: Visibility
    default_permission: PermissionLevel


class QuarantineAudienceEntry(BaseModel):
    """One user who could read the document once indexed, and why."""

    user_id: uuid.UUID
    name: str | None = None
    email: str
    permission: PermissionLevel
    via: str


class QuarantinePermissionCounts(BaseModel):
    """How many users in the full audience hold each permission level."""

    viewer: int
    editor: int
    manager: int


class QuarantineAudience(BaseModel):
    """Who gains access if the document is approved (capped at the server's limit).

    ``permission_counts`` aggregates the FULL audience (never redacted). ``entries`` carry
    per-user identities and are populated only for callers who manage the collection (or
    org admins); for other editors they are empty and ``note`` explains the redaction.
    """

    total_users: int
    truncated: bool
    note: str | None = None
    permission_counts: QuarantinePermissionCounts
    entries: list[QuarantineAudienceEntry]


class QuarantineReview(BaseModel):
    """Everything an editor needs to approve or discard a quarantined document:
    WHAT was detected (redacted), WHICH collection it goes into, WHO has access."""

    document: QuarantineDocument
    findings: list[SecretScanFinding]
    scanned_at: str | None = None
    truncated: bool
    collection: QuarantineCollection
    audience: QuarantineAudience
