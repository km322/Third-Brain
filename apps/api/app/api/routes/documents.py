"""Documents API: ingestion sources, listing, retrieval and lifecycle.

Documents are created from inline text, a remote URL or a file upload. In every case
the raw bytes are persisted to blob storage, a checksum is computed, a ``Document`` row
is created with status ``pending`` and an ingestion job is enqueued (with an inline
fallback so it still works without a worker).

Authorization:
* Adding/modifying/deleting documents requires ``EDITOR`` on the parent collection.
* Listing is filtered to documents the caller can actually view (collection
  visibility/grants, minus document-level ``PRIVATE`` denials) via the retrieval scope.
* Reads require ``VIEWER`` on the document.
* Reviewing/approving a quarantined document requires ``EDITOR`` on the document.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import (
    AuthContext,
    enforce_rate_limit,
    require_read_scope,
    require_write_scope,
)
from app.models.access import AccessGrant
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.entity import Entity
from app.models.enums import (
    AuditAction,
    DocumentStatus,
    PermissionLevel,
    ResourceType,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
    Visibility,
    permission_at_least,
)
from app.schemas.common import Message, Page, PaginationParams
from app.schemas.document import (
    DocumentChunkRead,
    DocumentContent,
    DocumentContentUpdate,
    DocumentItem,
    DocumentTextCreate,
    DocumentUrlCreate,
    DocumentVerify,
    QuarantineAudience,
    QuarantineAudienceEntry,
    QuarantineCollection,
    QuarantineDocument,
    QuarantinePermissionCounts,
    QuarantineReview,
    SecretScanFinding,
)
from app.services.dlp_scan import DLP_META_KEY
from app.services.dlp_scan import report_to_meta as dlp_report_to_meta
from app.services.dlp_scan import scan_text as dlp_scan_text
from app.services.entities import reap_orphan_entities
from app.services.extractors import extract_text, fetch_url
from app.services.ingestion import index_content, is_image_document
from app.services.metering import record_audit
from app.services.permissions import (
    build_retrieval_scope,
    document_audience,
    effective_permission,
    require_permission,
)
from app.services.secret_scan import SCAN_META_KEY, mark_approved, scan_text
from app.services.storage import build_storage_key, get_storage
from app.services.verification import compute_expiry, resolve_interval
from app.workers.queue import enqueue_ingest

router = APIRouter(prefix="/documents", tags=["documents"])

# Reject uploads larger than this to protect memory and storage.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_READ_CHUNK = 1024 * 1024


def _sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of ``data``; run in a worker thread as hashing ~25MB blocks the loop."""
    return hashlib.sha256(data).hexdigest()


def _strip_url_credentials(url: str) -> str:
    """Drop any ``user:pass@`` userinfo from a URL so credentials are never persisted.

    A URL like ``https://user:secret@host/doc`` would otherwise store the credentials in
    ``source_uri`` (and the title/audit log), visible to anyone who can read the document.
    ``fetch_url`` does not use URL userinfo for auth, so removing it changes nothing else.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


async def _read_upload_capped(file: UploadFile, limit: int) -> bytes:
    """Read an upload in chunks, aborting with 413 as soon as ``limit`` is exceeded.

    Reading the whole part into memory first and checking the size afterwards means a
    multi-gigabyte upload is fully materialized in RAM before it is rejected, which can
    OOM the worker. Streaming with a running total bounds memory to ``limit`` + one chunk.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds {limit} byte limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _require_collection_editor(
    db: AsyncSession, ctx: AuthContext, collection_id: uuid.UUID
) -> Collection:
    """Ensure the caller may add content to ``collection_id`` (EDITOR)."""
    collection = await db.get(Collection, collection_id)
    if collection is None or collection.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Collection not found")
    await require_permission(
        db, ctx, ResourceType.COLLECTION, collection_id, PermissionLevel.EDITOR
    )
    return collection


async def _get_viewable_document(
    db: AsyncSession, ctx: AuthContext, document_id: uuid.UUID, needed: PermissionLevel
) -> tuple[Document, PermissionLevel]:
    """Fetch an org-scoped document and enforce ``needed`` permission on it.

    Also returns the caller's effective level, so handlers that need it (e.g. the
    content endpoint's ``editable`` flag) reuse the permission walk just paid for
    instead of recomputing it.
    """
    document = await db.get(Document, document_id)
    if document is None or document.org_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="Document not found")
    level = await require_permission(db, ctx, ResourceType.DOCUMENT, document_id, needed)
    return document, level


# Visibility ordering for the "a document may not out-broaden its collection" ceiling.
_VISIBILITY_RANK = {
    Visibility.PRIVATE: 0,
    Visibility.TEAM: 1,
    Visibility.ORG: 2,
    Visibility.PUBLIC: 3,
}


async def _persist_document(
    db: AsyncSession,
    ctx: AuthContext,
    collection: Collection,
    *,
    title: str,
    data: bytes,
    source_type: SourceType,
    mime_type: str | None,
    filename: str | None,
    source_uri: str | None = None,
    visibility: Visibility | None = None,
) -> Document:
    """Store the bytes, create the row, bump the count, commit and enqueue ingestion."""
    if not data:
        raise HTTPException(status_code=400, detail="Document content is empty")

    # A document's own visibility may be MORE restrictive than its collection freely, but
    # BROADENING it beyond the collection's audience (e.g. an ORG document inside a PRIVATE
    # collection) re-shares content more widely, so it requires MANAGER on the collection. A
    # mere EDITOR (who may add content) must not be able to over-expose it past the collection.
    if (
        visibility is not None
        and _VISIBILITY_RANK[visibility] > _VISIBILITY_RANK[collection.visibility]
    ):
        have = await effective_permission(db, ctx, ResourceType.COLLECTION, collection.id)
        if not permission_at_least(have, PermissionLevel.MANAGER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Setting a document's visibility broader than its collection requires "
                    "'manager' permission on the collection."
                ),
            )

    checksum = await asyncio.to_thread(_sha256_hex, data)
    document = Document(
        org_id=ctx.org_id,
        collection_id=collection.id,
        created_by_id=ctx.user_id,
        title=title[:1024],
        source_type=source_type,
        source_uri=source_uri,
        mime_type=mime_type,
        visibility=visibility,
        status=DocumentStatus.PENDING,
        size_bytes=len(data),
        checksum=checksum,
    )
    db.add(document)
    await db.flush()  # assign document.id for the storage key

    key = build_storage_key(ctx.org_id, document.id, filename)
    await get_storage().save(key, data, mime_type)
    document.storage_key = key

    # Atomic increment so concurrent uploads to the same collection cannot lose updates
    # (a Python read-modify-write would let two requests both read N and write N+1).
    collection.document_count = Collection.document_count + 1

    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_CREATED.value,
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
        meta={"title": document.title, "collection_id": str(collection.id)},
    )
    await db.commit()
    await db.refresh(document)

    await enqueue_ingest(document.id)
    return document


@router.get("", response_model=Page[DocumentItem])
async def list_documents(
    collection_id: uuid.UUID | None = Query(default=None),
    status_filter: DocumentStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=255),
    via: str | None = Query(
        default=None,
        max_length=64,
        description="Filter by provenance marker (e.g. 'mcp' for agent-written documents).",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> Page[DocumentItem]:
    """List documents visible to the caller, newest first, with optional filters."""
    params = PaginationParams(page=page, page_size=page_size)
    scope = await build_retrieval_scope(
        db, ctx, collection_ids=[collection_id] if collection_id else None
    )

    filters = [Document.org_id == ctx.org_id]
    if not scope.all_access:
        allow = []
        if scope.collection_ids:
            allow.append(Document.collection_id.in_(scope.collection_ids))
        if scope.extra_document_ids:
            allow.append(Document.id.in_(scope.extra_document_ids))
        if not allow:
            return Page.create([], 0, params)
        filters.append(or_(*allow))
        if scope.denied_document_ids:
            filters.append(Document.id.notin_(scope.denied_document_ids))

    if collection_id is not None:
        filters.append(Document.collection_id == collection_id)
    if status_filter is not None:
        filters.append(Document.status == status_filter)
    if via is not None:
        # Additive provenance filter (e.g. only agent-written docs). ANDed onto the org +
        # ACL scope above, so it can only narrow the visible set, never widen it. ``meta``
        # is a generic JSON column, so use the type-agnostic ``as_string()`` accessor
        # (renders ``metadata ->> 'via'``) rather than the JSONB-only ``astext``.
        filters.append(Document.meta["via"].as_string() == via)
    if q:
        # Escape LIKE metacharacters so a title search for "100%" or "a_b" matches those
        # literals rather than treating % / _ as wildcards.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Document.title.ilike(f"%{escaped}%", escape="\\"))

    total = (
        await db.execute(select(func.count()).select_from(Document).where(*filters))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(Document)
                .where(*filters)
                .order_by(Document.created_at.desc())
                .offset(params.offset)
                .limit(params.limit)
            )
        )
        .scalars()
        .all()
    )
    items = [DocumentItem.model_validate(d) for d in rows]
    return Page.create(items, total, params)


@router.post("/text", response_model=DocumentItem, status_code=status.HTTP_201_CREATED)
async def create_document_from_text(
    payload: DocumentTextCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Create a document from an inline text/markdown body."""
    await enforce_rate_limit(ctx)
    collection = await _require_collection_editor(db, ctx, payload.collection_id)
    data = payload.content.encode("utf-8")
    document = await _persist_document(
        db,
        ctx,
        collection,
        title=payload.title,
        data=data,
        source_type=SourceType.TEXT,
        mime_type="text/plain",
        filename=f"{payload.title}.txt",
        visibility=payload.visibility,
    )
    return DocumentItem.model_validate(document)


@router.post("/url", response_model=DocumentItem, status_code=status.HTTP_201_CREATED)
async def create_document_from_url(
    payload: DocumentUrlCreate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Create a document by fetching a remote URL at creation time."""
    # Bound API-key callers: this endpoint makes the server fetch a remote URL and enqueues
    # (billable) embedding work, so it must be rate-limited like the other cost surfaces.
    await enforce_rate_limit(ctx)
    collection = await _require_collection_editor(db, ctx, payload.collection_id)
    url = str(payload.url)
    try:
        fetched = await fetch_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Persist a credential-free URL only (fetch already happened with the original).
    display_url = _strip_url_credentials(url)
    title = payload.title or fetched.title or display_url
    filename = display_url.rstrip("/").rsplit("/", 1)[-1] or "download"
    document = await _persist_document(
        db,
        ctx,
        collection,
        title=title,
        data=fetched.content,
        source_type=SourceType.URL,
        mime_type=fetched.mime_type,
        filename=filename,
        source_uri=display_url,
        visibility=payload.visibility,
    )
    return DocumentItem.model_validate(document)


@router.post("/upload", response_model=DocumentItem, status_code=status.HTTP_201_CREATED)
async def upload_document(
    collection_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    visibility: Visibility | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Create a document from a multipart file upload."""
    await enforce_rate_limit(ctx)
    collection = await _require_collection_editor(db, ctx, collection_id)
    data = await _read_upload_capped(file, MAX_UPLOAD_BYTES)
    filename = file.filename or "upload"
    mime_type = file.content_type or mimetypes.guess_type(filename)[0]
    document = await _persist_document(
        db,
        ctx,
        collection,
        title=filename,
        data=data,
        source_type=SourceType.FILE,
        mime_type=mime_type,
        filename=filename,
        visibility=visibility,
    )
    return DocumentItem.model_validate(document)


@router.get("/{document_id}", response_model=DocumentItem)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> DocumentItem:
    """Fetch a single document's metadata. Requires VIEWER."""
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.VIEWER)
    return DocumentItem.model_validate(document)


@router.get("/{document_id}/chunks", response_model=list[DocumentChunkRead])
async def get_document_chunks(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> list[DocumentChunkRead]:
    """List a document's indexed chunks in order. Requires VIEWER."""
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.VIEWER)
    if document.status == DocumentStatus.QUARANTINED:
        # Defense-in-depth: chunks left over from a previously indexed version of a
        # now-quarantined document are not readable until the document is approved.
        return []
    rows = (
        (
            await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )
        .scalars()
        .all()
    )
    return [DocumentChunkRead.model_validate(c) for c in rows]


@router.post("/{document_id}/reprocess", response_model=DocumentItem)
async def reprocess_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Re-run ingestion for a document (re-extract, re-chunk, re-embed). Requires EDITOR."""
    await enforce_rate_limit(ctx)
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    if document.status == DocumentStatus.QUARANTINED:
        # Reprocess would flip the document to PENDING and briefly re-expose any leftover
        # chunks; a quarantined document must only leave that state through the review flow.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Document is quarantined; review it (approve to index anyway, or discard) "
                "instead of reprocessing"
            ),
        )
    if not document.storage_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no stored source to reprocess",
        )
    document.status = DocumentStatus.PENDING
    document.error = None
    await db.commit()
    await db.refresh(document)

    await enqueue_ingest(document.id)
    return DocumentItem.model_validate(document)


@router.get("/{document_id}/content", response_model=DocumentContent)
async def get_document_content(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_read_scope()),
) -> DocumentContent:
    """The document's full source text (from blob storage), for the editor. VIEWER.

    Quarantined documents withhold content until reviewed, matching the chunks
    endpoint. Binary sources (PDF/DOCX/HTML) return their extracted text; saving an
    edit converts the document to plain text (the editor discloses this).
    """
    document, level = await _get_viewable_document(db, ctx, document_id, PermissionLevel.VIEWER)
    if document.status == DocumentStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is quarantined; its content is withheld until reviewed.",
        )
    if not document.storage_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no stored source content.",
        )
    if is_image_document(document):
        # An image has no editable source text; its indexed content IS the generated
        # description, so return that (read-only) rather than failing to extract bytes.
        rows = (
            (
                await db.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document.id)
                    .order_by(DocumentChunk.chunk_index.asc())
                )
            )
            .scalars()
            .all()
        )
        return DocumentContent(
            id=document.id,
            title=document.title,
            content="\n\n".join(chunk.content for chunk in rows),
            mime_type=document.mime_type,
            source_type=document.source_type,
            editable=False,
            permission=level,
            chunk_count=document.chunk_count,
        )

    data = await get_storage().load(document.storage_key)
    try:
        content = await asyncio.to_thread(
            extract_text,
            data,
            mime_type=document.mime_type,
            filename=document.storage_key.rsplit("/", 1)[-1],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Document content is not text: {exc}",
        ) from exc
    editable = not is_image_document(document) and permission_at_least(
        level, PermissionLevel.EDITOR
    )
    return DocumentContent(
        id=document.id,
        title=document.title,
        content=content,
        mime_type=document.mime_type,
        source_type=document.source_type,
        editable=editable,
        permission=level,
        chunk_count=document.chunk_count,
    )


@router.put("/{document_id}/content", response_model=DocumentItem)
async def update_document_content(
    document_id: uuid.UUID,
    payload: DocumentContentUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Replace the document's content and re-index it inline. Requires EDITOR.

    Mirrors the MCP ``update_knowledge`` contract: content is secret/DLP-scanned
    BEFORE any mutation (a flagged save is rejected and the document left untouched),
    the re-index runs inline so the response reflects the new chunks, and any prior
    verification is invalidated by the rewrite.
    """
    await enforce_rate_limit(ctx)
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    if document.status == DocumentStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Document is quarantined; review it (approve or discard) before editing"),
        )
    if is_image_document(document):
        # index_content overwrites the document's blob with the new text under the SAME
        # storage key, which for an image would destroy the original bytes irrecoverably.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Image documents are not text-editable; their indexed text is a generated "
                "description. Upload a replacement image instead."
            ),
        )

    # Scan BEFORE any mutation so a rejected save leaves the stored document untouched.
    if settings.SECRET_SCAN_ENABLED:
        report = await asyncio.to_thread(scan_text, payload.content)
        if report.flagged:
            labels = ", ".join(sorted({f.label for f in report.findings}))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Save rejected: the new content appears to contain secrets ({labels}). "
                    "The document was left unchanged. Remove them and save again."
                ),
            )
    dlp_report = (
        await asyncio.to_thread(dlp_scan_text, payload.content) if settings.DLP_ENABLED else None
    )
    if (
        dlp_report is not None
        and dlp_report.flagged
        and settings.DLP_DEFAULT_ACTION == "quarantine"
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Save rejected: the new content contains personal or confidential data and "
                "this deployment quarantines such content for review."
            ),
        )

    try:
        await index_content(db, ctx, document, payload.content, replace=True, via="api")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # Re-label sensitivity to match the NEW content (clear it when clean).
    if dlp_report is not None and dlp_report.flagged:
        if settings.DLP_DEFAULT_ACTION != "warn":
            document.sensitivity = dlp_report.sensitivity
        document.meta = {**(document.meta or {}), DLP_META_KEY: dlp_report_to_meta(dlp_report)}
    elif dlp_report is not None:
        document.sensitivity = SensitivityLevel.NONE
        if document.meta and DLP_META_KEY in document.meta:
            document.meta = {k: v for k, v in document.meta.items() if k != DLP_META_KEY}

    # A content rewrite invalidates any prior human verification.
    document.verification_status = VerificationStatus.UNVERIFIED
    document.verified_by_id = None
    document.verified_at = None
    document.expires_at = None

    await record_audit(
        db,
        ctx,
        "document.updated",
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
        meta={"via": "api"},
    )
    await db.commit()
    await db.refresh(document)
    return DocumentItem.model_validate(document)


@router.post("/{document_id}/verify", response_model=DocumentItem)
async def verify_document(
    document_id: uuid.UUID,
    payload: DocumentVerify,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Mark a document verified/authoritative with a review-by date. Requires EDITOR."""
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    now = datetime.now(UTC)
    interval = resolve_interval(payload.review_interval_days)
    document.verification_status = VerificationStatus.VERIFIED
    document.verified_by_id = ctx.user_id
    document.verified_at = now
    document.review_interval_days = interval
    document.expires_at = compute_expiry(now, interval)
    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_VERIFIED.value,
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
        meta={"review_interval_days": interval},
    )
    await db.commit()
    await db.refresh(document)
    return DocumentItem.model_validate(document)


@router.post("/{document_id}/unverify", response_model=DocumentItem)
async def unverify_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Clear a document's verification. Requires EDITOR."""
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    document.verification_status = VerificationStatus.UNVERIFIED
    document.verified_by_id = None
    document.verified_at = None
    document.review_interval_days = None
    document.expires_at = None
    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_UNVERIFIED.value,
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
    )
    await db.commit()
    await db.refresh(document)
    return DocumentItem.model_validate(document)


@router.get("/{document_id}/review", response_model=QuarantineReview)
async def review_quarantined_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> QuarantineReview:
    """Review a quarantined document before deciding its fate. Requires EDITOR.

    Returns WHAT was detected (redacted samples only, never raw secret material), WHICH
    collection the document would be indexed into, and WHO would gain access. Per-user
    identities in the audience are shown only to collection managers and org admins; other
    editors see the aggregate counts with an explanatory note. 409 when the document is not
    quarantined.
    """
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    if document.status != DocumentStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not quarantined",
        )
    collection = await db.get(Collection, document.collection_id)
    if collection is None:  # pragma: no cover - the FK guarantees the parent exists
        raise HTTPException(status_code=404, detail="Collection not found")

    scan = (document.meta or {}).get(SCAN_META_KEY) or {}
    entries, total_users, note, permission_counts = await document_audience(
        db, collection, document, limit=50
    )

    # The audience carries names + emails, which GET /orgs/members gates behind org-admin
    # and the grants list behind collection-manager. Match that: only a caller who manages
    # the collection (or an org admin) sees per-user identities; everyone else gets the
    # aggregate counts and a note. The counts themselves are never redacted.
    truncated = total_users > len(entries)
    caller_perm = await effective_permission(db, ctx, ResourceType.COLLECTION, collection.id)
    can_see_identities = ctx.is_admin or permission_at_least(caller_perm, PermissionLevel.MANAGER)
    if can_see_identities:
        entry_models = [
            QuarantineAudienceEntry(
                user_id=e.user_id,
                name=e.name,
                email=e.email,
                permission=e.permission,
                via=e.via,
            )
            for e in entries
        ]
    else:
        entry_models = []
        redaction_note = (
            "The full list of users who would gain access is visible to collection "
            "managers and org admins."
        )
        note = f"{note} {redaction_note}" if note else redaction_note

    return QuarantineReview(
        document=QuarantineDocument(
            id=document.id,
            title=document.title,
            status=document.status,
            source_type=document.source_type,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            created_at=document.created_at,
            visibility=document.visibility,
        ),
        findings=[SecretScanFinding.model_validate(f) for f in scan.get("findings", [])],
        scanned_at=scan.get("flagged_at"),
        truncated=bool(scan.get("truncated", False)),
        collection=QuarantineCollection(
            id=collection.id,
            name=collection.name,
            visibility=collection.visibility,
            default_permission=collection.default_permission,
        ),
        audience=QuarantineAudience(
            total_users=total_users,
            truncated=truncated,
            note=note,
            permission_counts=QuarantinePermissionCounts(**permission_counts),
            entries=entry_models,
        ),
    )


@router.post("/{document_id}/approve", response_model=DocumentItem)
async def approve_quarantined_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> DocumentItem:
    """Approve a quarantined document for indexing ("index anyway"). Requires EDITOR.

    The approval is stamped against the document's current checksum, so re-ingesting the
    same content indexes normally while any later content change invalidates it and
    quarantines again. 409 when the document is not quarantined.
    """
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)
    if document.status != DocumentStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not quarantined",
        )

    # Serialize against any in-flight ingest of this document on the same advisory lock the
    # ingest pipeline uses, so a redelivered/stale ingest job cannot re-quarantine the row
    # and clobber this approval stamp (nor vice-versa). Once the lock is held, refresh the
    # row and re-check its status: a concurrent ingest may have moved it on already.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))").bindparams(k=str(document.id))
    )
    await db.refresh(document)
    if document.status != DocumentStatus.QUARANTINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not quarantined",
        )
    document.meta = mark_approved(
        document.meta,
        checksum=document.checksum,
        user_id=str(ctx.user_id) if ctx.user_id else None,
        at=datetime.now(UTC).isoformat(),
    )
    document.status = DocumentStatus.PENDING
    document.error = None
    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_QUARANTINE_APPROVED.value,
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
        meta={"title": document.title, "checksum": document.checksum},
    )
    await db.commit()
    await db.refresh(document)

    await enqueue_ingest(document.id)
    return DocumentItem.model_validate(document)


@router.delete("/{document_id}", response_model=Message)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(require_write_scope()),
) -> Message:
    """Delete a document and its chunks. Requires EDITOR on the document."""
    document, _ = await _get_viewable_document(db, ctx, document_id, PermissionLevel.EDITOR)

    if document.storage_key:
        try:
            await get_storage().delete(document.storage_key)
        except Exception:  # pragma: no cover - blob cleanup is best-effort
            pass

    collection = await db.get(Collection, document.collection_id)
    if collection is not None:
        # Atomic decrement, clamped at zero, for the same reason as the create path.
        collection.document_count = func.greatest(Collection.document_count - 1, 0)

    # Grants targeting this document have no FK to it, so remove them explicitly or they
    # would strand and silently re-apply if a document's id were ever reused.
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.org_id == ctx.org_id,
            AccessGrant.resource_type == ResourceType.DOCUMENT,
            AccessGrant.resource_id == document.id,
        )
    )

    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_DELETED.value,
        resource_type=ResourceType.DOCUMENT.value,
        resource_id=document.id,
        meta={"title": document.title},
    )
    await db.delete(document)
    await db.flush()
    # The FK cascade removed this document's entity LINKS; entities left with no links at
    # all would otherwise linger in the index forever (invisible in the API, but stale).
    await reap_orphan_entities(db, Entity.org_id == ctx.org_id)
    await db.commit()
    return Message(detail="Document deleted")
