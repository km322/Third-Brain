"""Document ingestion pipeline.

``ingest_document`` takes a persisted ``Document`` (status ``pending``) and drives it
to ``indexed``:

    processing → extract text → chunk → embed (batched) → write chunk rows → indexed

It owns its own transaction (commits progress and the final state) so it can be run
from an arq worker, an inline background task, or a route. Any failure flips the
document to ``failed`` with a human-readable ``error`` message. When secret scanning is
enabled, extracted content that appears to contain credentials is parked at
``quarantined`` (before any chunking/embedding) until a human approves or discards it.
Chunk rows denormalize ``org_id`` and ``collection_id`` so permission-aware retrieval
can filter without joins.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from opentelemetry import trace
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import AuthContext
from app.core.logging import get_logger
from app.core.telemetry import get_tracer
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    AuditAction,
    ConnectorPurpose,
    DocumentStatus,
    OrgRole,
    SensitivityLevel,
    SourceType,
    UsageKind,
)
from app.services.chunking import chunk_text
from app.services.dlp_scan import DLP_META_KEY
from app.services.dlp_scan import report_to_meta as dlp_report_to_meta
from app.services.dlp_scan import scan_text as dlp_scan_text
from app.services.entities import sync_document_entities
from app.services.extractors import extract_text
from app.services.llm import ChatMessage, ImageAttachment, complete, embed_texts, resolver
from app.services.llm.pricing import (
    completion_cost,
    embedding_cost,
    is_billable_provider,
)
from app.services.metering import record_audit, record_usage
from app.services.secret_scan import (
    SCAN_META_KEY,
    is_approved,
    report_to_meta,
    scan_text,
    summarize_findings,
)
from app.services.storage import build_storage_key, get_storage

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Number of chunks embedded per provider call. Keeps request payloads bounded.
EMBED_BATCH_SIZE = 96

# Upper bound on embedding batches in flight at once. Overlapping batches hide per-call
# latency on a large document without flooding the provider or tripping its rate limits;
# kept small so a burst of ingestions across documents does not multiply into a stampede.
EMBED_CONCURRENCY = 4

# A document that has sat in PROCESSING longer than this has almost certainly been stranded
# by a crash (the inline-fallback path has no queue redelivery), so a reaper flips it to
# FAILED. Comfortably above the worker ``job_timeout`` (600s) so a slow-but-live job is never
# reaped out from under itself. PENDING is deliberately NOT reaped here: a queued-but-not-yet-
# started document is normal under a backlog and only means "waiting for a worker", not "crashed".
STUCK_DOCUMENT_TIMEOUT_SECONDS = 30 * 60


async def reap_stuck_documents(
    db: AsyncSession, *, older_than_seconds: int = STUCK_DOCUMENT_TIMEOUT_SECONDS
) -> int:
    """Flip documents stranded mid-ingestion (PROCESSING) to FAILED so they are visible + retryable.

    A document whose ingestion process was hard-killed (OOM, deploy, SIGKILL) while PROCESSING
    stays "processing" forever with no error and no redelivery; this surfaces it. PENDING
    documents are left alone - they are simply waiting in the queue, and failing them would
    wrongly report merely-backlogged work as a crash. Returns the number reaped.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    result = await db.execute(
        update(Document)
        .where(
            Document.status == DocumentStatus.PROCESSING,
            Document.updated_at < cutoff,
        )
        .values(
            status=DocumentStatus.FAILED,
            error="Ingestion did not complete (worker crash or timeout); reprocess to retry.",
        )
    )
    await db.commit()
    reaped = result.rowcount or 0
    if reaped:
        logger.info("stuck_documents_reaped", count=reaped)
    return reaped


# Images larger than this are not sent to the vision model (providers cap per-image
# payloads around 5MB); they still index with a metadata summary + capability link.
_MAX_VISION_IMAGE_BYTES = 4_500_000

# Magic-byte signatures for the raster formats we caption. The upload mime type is
# client-supplied, so bytes that don't match are never sent to a provider. WEBP is
# handled separately in ``sniffed_image_mime``: its RIFF prefix alone also matches
# non-image containers (WAV, AVI), so the format tag at offset 8 must be checked too.
_IMAGE_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("image/png", b"\x89PNG\r\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/gif", b"GIF8"),
)

_VISION_PROMPT = (
    "Describe this image for a searchable company knowledge base. Include: a concise "
    "summary of what it shows; ALL legible text transcribed verbatim; any diagram "
    "structure (components, arrows, relationships); and every name, label and number "
    "you can read. Be thorough and factual; do not speculate."
)


def is_image_document(doc: Document) -> bool:
    """Whether ``doc`` should take the image-description ingestion path."""
    return (doc.mime_type or "").lower().startswith("image/")


def sniffed_image_mime(data: bytes) -> str | None:
    """The image media type implied by magic bytes, or ``None`` for non-image bytes."""
    for mime, signature in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_file_url(token: str) -> str:
    """Absolute capability URL serving the original image bytes."""
    base = settings.PUBLIC_API_URL.rstrip("/")
    return f"{base}{settings.API_V1_PREFIX}/files/{token}"


def _compose_image_content(doc: Document, caption: str, url: str) -> str:
    """The indexed text for an image document: summary first, capability link last.

    The link line is part of the chunk text on purpose - any LLM that retrieves this
    chunk can fetch the original image for its own vision context.
    """
    size_kb = max(1, (doc.size_bytes or 0) // 1024)
    return (
        f"[Image] {doc.title} ({doc.mime_type}, {size_kb} KB)\n\n"
        f"{caption.strip()}\n\n"
        f"Original image (fetch for full visual context): {url}"
    )


async def _describe_image(db: AsyncSession, doc: Document) -> str:
    """Produce the indexed text for an image document.

    Loads the original bytes, captions + OCR-transcribes them via the org's completion
    provider (through the LLM facade, so connectors/platform keys/offline fallback all
    apply), stamps a capability token into ``doc.meta`` and returns the composed
    summary. Degrades to a metadata-only summary - never fails ingestion - when no
    vision-capable provider is live, the bytes don't look like an image, or the image
    is too large to send.
    """
    data = await get_storage().load(doc.storage_key)
    sniffed = sniffed_image_mime(data)

    url = ""
    if sniffed is not None:
        meta = dict(doc.meta or {})
        token = meta.get("file_token")
        if not token:
            token = secrets.token_urlsafe(32)
            # Wholesale reassignment marks the JSON column dirty (in-place mutation doesn't).
            doc.meta = {**meta, "file_token": token}
        url = _image_file_url(token)

    caption: str | None = None
    if sniffed is not None and len(data) <= _MAX_VISION_IMAGE_BYTES:
        res = await resolver.resolve(db, doc.org_id, ConnectorPurpose.COMPLETION)
        result = await complete(
            [
                ChatMessage(
                    role="user",
                    content=_VISION_PROMPT,
                    images=[
                        ImageAttachment(
                            media_type=sniffed,
                            data_b64=base64.b64encode(data).decode("ascii"),
                        )
                    ],
                )
            ],
            res.model,
            api_key=res.api_key,
            api_base=res.api_base,
            provider=res.provider,
            max_tokens=1024,
        )
        if is_billable_provider(result.provider) and result.text.strip():
            caption = result.text
            usage_ctx = AuthContext(org_id=doc.org_id, org_role=OrgRole.ADMIN)
            # Committed on its own session: the provider was really billed, so the record must
            # survive a later failure (e.g. embedding) rolling back the ingest transaction.
            async with SessionLocal() as usage_db:
                await record_usage(
                    usage_db,
                    usage_ctx,
                    UsageKind.COMPLETION,
                    provider=result.provider,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    units=1,
                    cost_usd=completion_cost(result.model, result.tokens_in, result.tokens_out),
                    latency_ms=result.latency_ms,
                    meta={"document_id": str(doc.id), "op": "image_caption"},
                )
                await usage_db.commit()
        else:
            logger.info(
                "image_caption_unavailable",
                document_id=str(doc.id),
                provider=result.provider,
            )

    if sniffed is None:
        # Declared image/* but the bytes are not a raster image we can caption. NEVER index a
        # placeholder here: the real bytes would then reach the public capability URL without
        # ever passing the secret/DLP gate (the only text scanned is the summary we compose).
        # Extract the content for real instead - SVG/text-ish payloads index their markup, and
        # anything undecodable raises, failing the document loudly as it did before images
        # were supported.
        text_content = await asyncio.to_thread(
            extract_text,
            data,
            mime_type=None,
            filename=doc.storage_key.rsplit("/", 1)[-1],
        )
        return text_content

    if caption is None:
        caption = (
            "No vision-capable completion provider was available when this image was "
            "ingested, so only this metadata summary is indexed. The original image is "
            "available at the link below; reprocess this document after configuring a "
            "completion connector to generate a full description."
        )
    return _compose_image_content(doc, caption, url)


async def _load_text(db: AsyncSession, doc: Document) -> str:
    """Recover the document's plain text from its stored bytes.

    Extraction is CPU-bound (PDF/DOCX parsing, large decodes) and can run for many
    seconds, so it is offloaded to a worker thread rather than blocking the event loop and
    stalling every other request/heartbeat on the process.
    """
    if not doc.storage_key:
        raise ValueError("Document has no stored content to ingest")
    data = await get_storage().load(doc.storage_key)
    # Use the stored object's basename (the real ingested filename with its true extension),
    # not the user-facing title, so extension sniffing never trusts an arbitrary title like
    # "Q3 report.pdf" on a plain-text document.
    filename = doc.storage_key.rsplit("/", 1)[-1] or doc.source_uri
    return await asyncio.to_thread(
        extract_text,
        data,
        mime_type=doc.mime_type,
        filename=filename,
    )


async def embed_in_batches(
    texts: list[str],
    model: str | None,
    api_key: str | None,
    api_base: str | None,
    provider: str | None = None,
):
    """Embed ``texts`` in batches, returning (vectors, total_tokens, provider, model, latency_ms).

    Batches run with bounded concurrency (``EMBED_CONCURRENCY``) so a large document's
    calls overlap instead of running strictly one at a time, while a semaphore keeps the
    provider from being overwhelmed. Results are reassembled in input order, so each
    returned vector still lines up with its originating chunk. ``latency_ms`` aggregates
    the provider time across all batches.
    """
    batches = [
        texts[start : start + EMBED_BATCH_SIZE] for start in range(0, len(texts), EMBED_BATCH_SIZE)
    ]
    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def _embed_batch(batch: list[str]):
        async with semaphore:
            return await embed_texts(
                batch, model=model, api_key=api_key, api_base=api_base, provider=provider
            )

    # gather preserves the order of the supplied awaitables regardless of completion
    # order, so extending in sequence keeps vectors aligned to their input chunks.
    results = await asyncio.gather(*(_embed_batch(batch) for batch in batches))

    vectors: list[list[float]] = []
    total_tokens = 0
    provider = ""
    used_model = model or ""
    latency_ms = 0
    for result in results:
        vectors.extend(result.vectors)
        total_tokens += result.tokens
        provider = result.provider
        used_model = result.model
        latency_ms += result.latency_ms
    return vectors, total_tokens, provider, used_model, latency_ms


async def _mark_failed(db: AsyncSession, doc_id: uuid.UUID, message: str) -> None:
    """Roll back and record ``doc_id`` as FAILED in a fresh transaction."""
    await db.rollback()
    failed = await db.get(Document, doc_id)
    if failed is not None:
        failed.status = DocumentStatus.FAILED
        failed.error = message[:2000]
        await db.commit()
        logger.warning("ingestion_status", status="failed")


async def ingest_document(db: AsyncSession, doc_id: uuid.UUID) -> str:
    """Extract, chunk, embed and index a single document.

    Commits its own writes. Safe to re-run (existing chunks are replaced), which is
    exactly what the ``reprocess`` endpoint relies on. Returns the document's final
    status value (``"indexed"``, ``"quarantined"``, ``"failed"``, or ``"missing"`` when
    the row is gone).
    """
    started = time.monotonic()
    doc = await db.get(Document, doc_id)
    if doc is None:
        logger.warning("ingest_document: document %s not found", doc_id)
        return "missing"

    # Worker/inline contexts carry no auth binding, so attribute log lines to the org here.
    structlog.contextvars.bind_contextvars(org_id=str(doc.org_id))
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("document_id", str(doc.id))

    # Mark as processing so the UI reflects work in flight immediately.
    doc.status = DocumentStatus.PROCESSING
    doc.error = None
    await db.commit()
    logger.info(
        "ingestion_status",
        document_id=str(doc.id),
        status="processing",
    )

    try:
        # Serialize concurrent ingestions of the SAME document (double-clicked reprocess,
        # a redelivered job racing the original) on a transaction-scoped advisory lock, so
        # two runs cannot both delete-then-insert and leave the document with two full sets
        # of chunks. The lock is held until this transaction commits below.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))").bindparams(k=str(doc.id))
        )
        # A redelivered/stale job may hold an out-of-date view of the row (identity-map
        # cache from before an approve committed). Refresh it under the lock so the scan
        # decision below sees the current meta/checksum/status rather than clobbering a
        # fresh approval stamp.
        await db.refresh(doc)

        with tracer.start_as_current_span("ingest.load_extract"):
            if is_image_document(doc):
                content = await _describe_image(db, doc)
            else:
                content = await _load_text(db, doc)

        # Quarantine gate: content that appears to contain secrets never reaches
        # chunking/embedding (and any previously indexed chunks are left untouched)
        # until a human approves it. Approval is keyed to the checksum, so unchanged
        # content re-ingests normally after approval while any content change
        # invalidates the approval and re-quarantines. Approval is checked FIRST so an
        # already-approved, unchanged document skips the scan entirely - there is no
        # point re-scanning content a human has explicitly cleared.
        if settings.SECRET_SCAN_ENABLED and not is_approved(doc.meta, doc.checksum):
            with tracer.start_as_current_span("ingest.secret_scan"):
                # Scanning is CPU-bound (regex over the whole document); offload it so a
                # large document does not stall the event loop, matching ``chunk_text``.
                report = await asyncio.to_thread(scan_text, content)
            if report.flagged:
                detectors, occurrences = summarize_findings(report)
                doc.status = DocumentStatus.QUARANTINED
                doc.error = None
                doc.meta = {
                    **doc.meta,
                    SCAN_META_KEY: report_to_meta(
                        report,
                        checksum=doc.checksum,
                        flagged_at=datetime.now(UTC).isoformat(),
                    ),
                }
                await record_audit(
                    db,
                    None,
                    AuditAction.DOCUMENT_QUARANTINED.value,
                    org_id=doc.org_id,
                    resource_type="document",
                    resource_id=str(doc.id),
                    meta={"detectors": detectors, "occurrences": occurrences},
                )
                await db.commit()
                logger.warning(
                    "ingestion_status",
                    document_id=str(doc.id),
                    status="quarantined",
                    detectors=detectors,
                    occurrences=occurrences,
                )
                return DocumentStatus.QUARANTINED.value
            if SCAN_META_KEY in doc.meta:
                # A clean rescan clears any stale scan payload/approval stamp.
                doc.meta = {k: v for k, v in doc.meta.items() if k != SCAN_META_KEY}

        # DLP / PII classification. Runs after the secret gate (a secret-quarantined doc has
        # already returned). ``label`` (default) tags ``sensitivity`` and indexes; ``quarantine``
        # parks for review, honouring the SAME checksum-keyed approval as secrets so an
        # approved doc indexes; ``warn`` records findings without labelling.
        if settings.DLP_ENABLED:
            with tracer.start_as_current_span("ingest.dlp_scan"):
                dlp_report = await asyncio.to_thread(dlp_scan_text, content)
            if dlp_report.flagged:
                doc.meta = {**doc.meta, DLP_META_KEY: dlp_report_to_meta(dlp_report)}
                action = settings.DLP_DEFAULT_ACTION
                if action == "quarantine" and not is_approved(doc.meta, doc.checksum):
                    doc.status = DocumentStatus.QUARANTINED
                    doc.error = None
                    await record_audit(
                        db,
                        None,
                        AuditAction.DOCUMENT_SENSITIVE.value,
                        org_id=doc.org_id,
                        resource_type="document",
                        resource_id=str(doc.id),
                        meta={"sensitivity": dlp_report.sensitivity.value, "action": "quarantine"},
                    )
                    await db.commit()
                    logger.warning(
                        "ingestion_status",
                        document_id=str(doc.id),
                        status="quarantined",
                        reason="dlp",
                        sensitivity=dlp_report.sensitivity.value,
                    )
                    return DocumentStatus.QUARANTINED.value
                if action != "warn":
                    doc.sensitivity = dlp_report.sensitivity
                await record_audit(
                    db,
                    None,
                    AuditAction.DOCUMENT_SENSITIVE.value,
                    org_id=doc.org_id,
                    resource_type="document",
                    resource_id=str(doc.id),
                    meta={"sensitivity": dlp_report.sensitivity.value, "action": action},
                )
            else:
                # A clean rescan clears any prior label/findings.
                if doc.sensitivity != SensitivityLevel.NONE:
                    doc.sensitivity = SensitivityLevel.NONE
                if DLP_META_KEY in doc.meta:
                    doc.meta = {k: v for k, v in doc.meta.items() if k != DLP_META_KEY}

        # Resolve the embedding connector only now that the document is cleared to be
        # indexed: a quarantined document returns above without ever needing it, so the
        # resolve is not wasted on content that will not be embedded. Embed through the
        # org's default embedding connector (or the platform default when it has none) -
        # the SAME path retrieval uses for the query - so index and query vectors always
        # come from one provider/model and cosine search stays meaningful.
        collection = await db.get(Collection, doc.collection_id)
        res = await resolver.resolve(db, doc.org_id, ConnectorPurpose.EMBEDDING)
        embedding_model = res.model or (collection.embedding_model if collection else None)

        with tracer.start_as_current_span("ingest.chunk") as chunk_span:
            chunks = await asyncio.to_thread(chunk_text, content, model=embedding_model)
            chunk_span.set_attribute("chunk_count", len(chunks))

        # Replace any prior chunks (reprocess) before inserting the new set.
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))

        if not chunks:
            # No text was extracted. Reporting this as a successful 0-chunk index hides
            # real failures (e.g. a scanned/image-only PDF with no text layer, or an
            # extractor that produced nothing), so surface it as a failure the user sees.
            raise ValueError("No extractable text found in the document")

        with tracer.start_as_current_span("ingest.embed") as embed_span:
            vectors, total_tokens, provider, used_model, embed_latency_ms = await embed_in_batches(
                [c.content for c in chunks],
                embedding_model,
                res.api_key,
                res.api_base,
                res.provider,
            )
            embed_span.set_attribute("embed_tokens", total_tokens)
            embed_span.set_attribute("embed_provider", provider)
            embed_span.set_attribute("embed_model", used_model)

        # A conforming provider returns exactly one vector per input; a mismatch means
        # part of the document would be silently dropped from the index.
        if len(vectors) != len(chunks):
            raise ValueError(
                f"Embedding provider returned {len(vectors)} vectors for {len(chunks)} chunks"
            )

        with tracer.start_as_current_span("ingest.index"):
            for chunk, vector in zip(chunks, vectors, strict=True):
                db.add(
                    DocumentChunk(
                        org_id=doc.org_id,
                        collection_id=doc.collection_id,
                        document_id=doc.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding=vector,
                        meta={},
                    )
                )

            doc.status = DocumentStatus.INDEXED
            doc.chunk_count = len(chunks)
            doc.indexed_at = datetime.now(UTC)
            doc.error = None

            # Attribute ingestion cost to the owning org for analytics/billing.
            ctx = AuthContext(org_id=doc.org_id, org_role=OrgRole.ADMIN)
            await record_usage(
                db,
                ctx,
                UsageKind.INGEST,
                provider=provider,
                model=used_model,
                tokens_in=total_tokens,
                units=len(chunks),
                cost_usd=(
                    embedding_cost(used_model, total_tokens)
                    if is_billable_provider(provider)
                    else 0.0
                ),
                latency_ms=embed_latency_ms,
                meta={"document_id": str(doc.id)},
            )

            await db.commit()

        # Entity enrichment (feature: NER). Non-fatal: a hiccup here must never fail an
        # otherwise-indexed document, so it commits separately and swallows errors.
        if settings.ENTITY_EXTRACTION_ENABLED:
            try:
                with tracer.start_as_current_span("ingest.entities"):
                    await sync_document_entities(db, doc.org_id, doc.id, content, title=doc.title)
                await db.commit()
            except Exception as exc:  # pragma: no cover - enrichment is best-effort
                logger.warning("entity_extraction_failed", document_id=str(doc.id), error=str(exc))
                await db.rollback()

        logger.info(
            "ingestion_status",
            document_id=str(doc.id),
            status="indexed",
            chunk_count=len(chunks),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return DocumentStatus.INDEXED.value
    except asyncio.CancelledError:
        # A worker timeout or graceful shutdown cancelled the job. PROCESSING was already
        # committed up front, so flip the document out of it (best effort) before re-raising.
        # This surfaces the failure immediately; a hard kill that skips this handler is still
        # caught later by reap_stuck_documents, but only after STUCK_DOCUMENT_TIMEOUT_SECONDS.
        await _mark_failed(db, doc_id, "Ingestion was interrupted (timeout or shutdown).")
        raise
    except Exception as exc:
        logger.exception(
            "ingestion_failed",
            document_id=str(doc_id),
            status="failed",
            error=str(exc)[:2000],
        )
        await _mark_failed(db, doc_id, str(exc)[:2000])
        return DocumentStatus.FAILED.value


async def index_content(
    db: AsyncSession,
    ctx: AuthContext,
    document: Document,
    content: str,
    *,
    replace: bool = False,
    via: str = "api",
) -> int:
    """Persist ``content`` as the document's source blob, then chunk + embed + index it
    inline (no worker round-trip). Returns the number of chunks written.

    Shared by the REST content-save endpoint and the MCP ``add/update_knowledge`` tools
    so every write path chunks identically and lands in the same embedding space. Records
    embedding usage tagged ``via`` and re-syncs the entity index to the new content
    (best-effort, like the worker path). Raises ``ValueError`` on empty/unembeddable
    content; the caller commits (or rolls back) the transaction.

    The source bytes are written to storage and ``storage_key``/``checksum`` updated so a
    later ``reprocess`` re-ingests THIS content rather than reverting to stale bytes.
    """
    # Serialize against the ingestion worker and any concurrent save on this document:
    # both do delete-then-insert over the chunk set, which interleaves into duplicated or
    # clobbered chunks without the lock. Released at the caller's commit/rollback.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))").bindparams(k=str(document.id))
    )
    collection = await db.get(Collection, document.collection_id)
    res = await resolver.resolve(db, ctx.org_id, ConnectorPurpose.EMBEDDING)
    embedding_model = res.model or (collection.embedding_model if collection else None)

    # Chunking is CPU-bound; run it off the event loop so a large write does not stall
    # every other request on this process.
    chunks = await asyncio.to_thread(chunk_text, content, model=embedding_model)
    if not chunks:
        raise ValueError("Content produced no indexable text")

    if replace:
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))

    document.status = DocumentStatus.PROCESSING
    await db.flush()

    # Embed FIRST (batched, so a large document doesn't blow the provider's per-request input
    # cap) - before persisting the new source blob. If embedding fails the caller rolls the
    # transaction back; writing storage first would leave the blob ahead of the DB, so a later
    # reprocess would silently index the content of an update that reported failure.
    vectors, total_tokens, provider, used_model, latency_ms = await embed_in_batches(
        [c.content for c in chunks], embedding_model, res.api_key, res.api_base, res.provider
    )
    if len(vectors) != len(chunks):  # pragma: no cover - provider contract guard
        raise ValueError("Embedding provider returned a mismatched number of vectors")

    # Embedding succeeded: now persist the source so reprocess and future writes share one
    # source of truth, consistent with the chunks about to be written.
    data = content.encode("utf-8")
    key = document.storage_key or build_storage_key(
        ctx.org_id, document.id, f"{document.title}.txt"
    )
    await get_storage().save(key, data, "text/plain")
    document.storage_key = key
    document.checksum = hashlib.sha256(data).hexdigest()
    # This overwrite replaced the blob with plain-text bytes, so normalize the document's
    # stored representation to match. Keeping a stale mime_type/source_type (e.g. a PDF/DOCX/URL
    # doc updated inline) would make a later reprocess run the wrong extractor over text bytes
    # and fail; text/plain is authoritative in extract_text regardless of the storage-key ext.
    document.mime_type = "text/plain"
    document.source_type = SourceType.TEXT

    for chunk, vector in zip(chunks, vectors, strict=True):
        db.add(
            DocumentChunk(
                org_id=ctx.org_id,
                collection_id=document.collection_id,
                document_id=document.id,
                chunk_index=chunk.index,
                content=chunk.content,
                token_count=chunk.token_count,
                embedding=vector,
                meta={},
            )
        )

    document.chunk_count = len(chunks)
    document.size_bytes = len(data)
    document.status = DocumentStatus.INDEXED
    document.indexed_at = datetime.now(UTC)
    document.error = None
    await db.flush()

    await record_usage(
        db,
        ctx,
        UsageKind.EMBEDDING,
        provider=provider,
        model=used_model,
        tokens_in=total_tokens,
        units=len(chunks),
        cost_usd=(
            embedding_cost(used_model, total_tokens) if is_billable_provider(provider) else 0.0
        ),
        latency_ms=latency_ms,
        meta={"document_id": str(document.id), "via": via},
    )

    # Keep the entity index in step with the rewritten content, mirroring the worker
    # ingest path (feature: NER). Best-effort on a savepoint: an extraction hiccup must
    # never fail the save, and rolling back only the savepoint leaves the caller's
    # transaction (chunks, blob bookkeeping, usage) intact.
    if settings.ENTITY_EXTRACTION_ENABLED:
        try:
            async with db.begin_nested():
                await sync_document_entities(
                    db, ctx.org_id, document.id, content, title=document.title
                )
        except Exception as exc:  # pragma: no cover - enrichment is best-effort
            logger.warning("entity_extraction_failed", document_id=str(document.id), error=str(exc))

    return len(chunks)
