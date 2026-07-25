"""Model Context Protocol tools backed by the Third Brain knowledge base.

Each tool is a thin, permission-aware wrapper over the same spine services the REST
API uses. Nothing here bypasses authorization:

* **Retrieval** delegates to :func:`app.services.retrieval.retrieve`, which resolves the
  caller's visible-chunk universe via :func:`app.services.permissions.build_retrieval_scope`
  and the vector store (so a chunk the caller may not view can never enter a result) and
  caches the query embedding; it additionally requires the API key to carry a ``search``
  scope (parity with REST).
* **Reads** (`get_document`, `list_collections`) verify effective permission via
  :func:`app.services.permissions.effective_permission`, and additionally require the API
  key to carry a read scope (parity with REST's read surface).
* **Writes** (`add_knowledge`, `update_knowledge`) require ``editor`` permission via
  :func:`app.services.permissions.require_permission`, and additionally require the
  API key to carry an ``ingest``/``write`` scope.

Every query is org-scoped through the resolved :class:`AuthContext`. Ingestion (chunk →
embed → persist) is done inline via the shared LLM + vector primitives so the module is
self-contained and runs fully offline (the LLM facade falls back to a deterministic
provider when no key is configured).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import READ_SCOPES, AuthContext, role_at_least
from app.core.logging import get_logger
from app.models.access import AccessGrant
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    AuditAction,
    ConnectorPurpose,
    DocumentStatus,
    OrgRole,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    SensitivityLevel,
    SourceType,
    UsageKind,
    VerificationStatus,
    Visibility,
    max_permission,
    permission_at_least,
)
from app.schemas.document import MAX_TEXT_CONTENT_CHARS
from app.services.dlp_scan import DLP_META_KEY
from app.services.dlp_scan import report_to_meta as dlp_report_to_meta
from app.services.dlp_scan import scan_text as dlp_scan_text
from app.services.ingestion import index_content
from app.services.llm import ChatMessage, complete, resolver
from app.services.llm.pricing import completion_cost, is_billable_provider
from app.services.metering import record_audit, record_usage
from app.services.permissions import (
    _visibility_grants_access,
    effective_permission,
    require_permission,
    user_team_ids,
)
from app.services.retrieval import retrieve
from app.services.secret_scan import (
    SCAN_META_KEY,
    ScanReport,
    report_to_meta,
    scan_text,
    summarize_findings,
)
from app.services.storage import build_storage_key, get_storage

logger = get_logger(__name__)

_SNIPPET_CHARS = 600
_MAX_TOP_K = 50

# Auto-routing (add_knowledge with no explicit collection): an LLM classifier files the capture
# into the caller's best-matching editable collection; a shared "Decisions" collection is the
# fallback home when nothing fits (created on first use).
_CLASSIFY_CONTENT_CHARS = 1200
_MAX_CLASSIFY_CANDIDATES = 25
_DECISIONS_SLUG = "decisions"
_DECISIONS_NAME = "Decisions"
_DOC_TYPES = frozenset({"decision", "solution", "answer", "note", "reference"})


class ToolError(Exception):
    """A user-facing tool failure (bad input, not found, permission denied).

    Surfaced to MCP clients as a tool result with ``isError: true`` rather than a
    transport-level JSON-RPC error, so the calling model can read and react to it.
    """


# --------------------------------------------------------------------------- #
# Tool schema definitions (advertised via MCP ``tools/list``)
# --------------------------------------------------------------------------- #
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_knowledge",
        "description": (
            "Semantic + keyword search across the knowledge base the caller can access. "
            "Search here to reuse what the team already knows before answering or acting. "
            "Returns ranked snippets with document titles and ids to cite or fetch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language question or search phrase.",
                },
                "collection": {
                    "type": "string",
                    "description": "Optional collection id, slug, or name to restrict the search.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_TOP_K,
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_document",
        "description": (
            "Fetch a single document's full indexed text and metadata by id. "
            "Requires at least viewer permission on the document."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "UUID of the document to fetch.",
                }
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_collections",
        "description": (
            "List the knowledge collections (knowledge bases) the caller can access, "
            "with the caller's effective permission on each."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "add_knowledge",
        "description": (
            "Capture knowledge back into the team's brain as you work - proactively, without "
            "being asked. Whenever you make a decision, resolve a tradeoff, solve a problem, "
            "or produce a reusable answer or piece of documentation, record it here so the "
            "team's documentation stays current. You do NOT need to specify a collection: "
            "omit it and Third Brain files the document in the best-matching collection "
            "automatically. Set doc_type to categorize the capture (e.g. 'decision'). "
            "Requires editor permission. Content that appears to contain secrets is "
            "quarantined for human review instead of indexed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short document title."},
                "content": {
                    "type": "string",
                    "description": "Full text/markdown body to index.",
                },
                "collection": {
                    "type": "string",
                    "description": (
                        "Optional target collection id, slug, or name. Omit to let Third "
                        "Brain file it in the best-matching collection automatically."
                    ),
                },
                "doc_type": {
                    "type": "string",
                    "enum": ["decision", "solution", "answer", "note", "reference"],
                    "description": "Optional category for the capture, e.g. 'decision'.",
                },
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_knowledge",
        "description": (
            "Replace the content of an existing document and re-index it - use this to "
            "keep previously captured knowledge up to date as decisions or details change. "
            "Requires editor permission on the document. Updates whose new content appears "
            "to contain secrets are rejected without changing the document."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "UUID of the document to update.",
                },
                "content": {
                    "type": "string",
                    "description": "New full text/markdown body (replaces the old content).",
                },
            },
            "required": ["document_id", "content"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOL_DEFINITIONS)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolError(f"Argument '{key}' is required and must be a string")
    value = value.strip()
    if not value:
        raise ToolError(f"Argument '{key}' must not be empty")
    return value


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ToolError(f"'{field}' must be a valid UUID") from exc


def _require_write_scope(ctx: AuthContext) -> None:
    """Defense-in-depth: writing knowledge needs an ingest-capable API key.

    Human sessions and wildcard keys pass automatically (``has_scope`` returns True).
    """
    if not (ctx.has_scope("ingest") or ctx.has_scope("write")):
        raise ToolError("This API key is missing the 'ingest' scope required to modify knowledge")


def _require_search_scope(ctx: AuthContext) -> None:
    """Parity with REST ``/search`` and ``/v1``: searching needs a search-capable API key.

    Human sessions and wildcard keys pass automatically (``has_scope`` returns True).
    """
    if not ctx.has_scope("search"):
        raise ToolError("This API key is missing the 'search' scope required to search knowledge")


def _require_read_scope(ctx: AuthContext) -> None:
    """Parity with REST's read surface: reading knowledge needs a read-capable API key.

    Mirrors REST ``READ_SCOPES`` - any recognized capability grants read (``write``/``ingest``
    imply read), so an empty-scoped key can neither read nor write. Human sessions and
    wildcard keys pass automatically (``has_scope`` returns True).
    """
    if not any(ctx.has_scope(scope) for scope in READ_SCOPES):
        raise ToolError("This API key is missing a read scope required to read knowledge")


async def _resolve_collection(db: AsyncSession, ctx: AuthContext, ref: str) -> Collection:
    """Resolve a collection by UUID, slug, or name within the caller's org."""
    ref = ref.strip()
    try:
        cid = uuid.UUID(ref)
    except ValueError:
        cid = None

    if cid is not None:
        collection = await db.get(Collection, cid)
        if collection is not None and collection.org_id == ctx.org_id:
            return collection

    rows = (
        (
            await db.execute(
                select(Collection).where(
                    Collection.org_id == ctx.org_id,
                    (Collection.slug == ref) | (Collection.name == ref),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise ToolError(f"Collection not found: {ref!r}")
    # Prefer an exact slug match if several names collide.
    for c in rows:
        if c.slug == ref:
            return c
    return rows[0]


# --------------------------------------------------------------------------- #
# Ingestion (chunk -> embed -> persist), shared by add/update
# --------------------------------------------------------------------------- #
async def _index_document(
    db: AsyncSession,
    ctx: AuthContext,
    document: Document,
    content: str,
    *,
    replace: bool = False,
) -> int:
    """Chunk + embed + index ``content`` via the shared :func:`ingestion.index_content`
    spine (same chunking, same embedding space as REST writes), translating its
    ``ValueError`` contract into MCP ``ToolError``. Records usage tagged ``via=mcp``.
    """
    try:
        return await index_content(db, ctx, document, content, replace=replace, via="mcp")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


async def _quarantine_new_document(
    db: AsyncSession,
    ctx: AuthContext,
    collection: Collection,
    title: str,
    content: str,
    report: ScanReport | None = None,
    *,
    dlp_report: Any | None = None,
) -> dict[str, Any]:
    """Persist a flagged MCP document as QUARANTINED without indexing any of it.

    Flagged either by the secret scanner (``report``) or the DLP/PII scanner
    (``dlp_report``), mirroring the ingestion worker's two gates. The blob, storage key and
    checksum are written exactly as the indexed path writes them, so a later dashboard
    approval re-ingests THIS content through the worker; only the chunks (and ``indexed_at``)
    are withheld. Raw content never appears in the response, the meta payload, the audit
    trail, or logs - redacted samples only. The caller (the MCP server) commits.
    """
    data = content.encode("utf-8")
    checksum = hashlib.sha256(data).hexdigest()
    meta: dict[str, Any] = {"via": "mcp"}
    if report is not None:
        meta[SCAN_META_KEY] = report_to_meta(
            report, checksum=checksum, flagged_at=datetime.now(UTC).isoformat()
        )
    if dlp_report is not None:
        meta[DLP_META_KEY] = dlp_report_to_meta(dlp_report)
    document = Document(
        org_id=ctx.org_id,
        collection_id=collection.id,
        created_by_id=ctx.user_id,
        title=title[:1024],
        source_type=SourceType.TEXT,
        status=DocumentStatus.QUARANTINED,
        mime_type="text/plain",
        size_bytes=len(data),
        checksum=checksum,
        chunk_count=0,
        meta=meta,
    )
    db.add(document)
    await db.flush()

    key = build_storage_key(ctx.org_id, document.id, f"{document.title}.txt")
    await get_storage().save(key, data, "text/plain")
    document.storage_key = key

    # Atomic increment so concurrent writes to the same collection cannot lose updates.
    collection.document_count = Collection.document_count + 1

    if report is not None:
        detectors, occurrences = summarize_findings(report)
        audit_action = AuditAction.DOCUMENT_QUARANTINED.value
        audit_meta = {
            "collection_id": str(collection.id),
            "detectors": detectors,
            "occurrences": occurrences,
            "via": "mcp",
        }
        findings_src = report.findings
        message = (
            "Possible secrets were detected, so this document was quarantined for human "
            "review before indexing. It is not searchable. An editor can review it on "
            "the dashboard Documents page and approve or discard it."
        )
    else:
        audit_action = AuditAction.DOCUMENT_SENSITIVE.value
        audit_meta = {
            "collection_id": str(collection.id),
            "sensitivity": dlp_report.sensitivity.value,
            "action": "quarantine",
            "via": "mcp",
        }
        findings_src = dlp_report.findings
        message = (
            "Personal or confidential data was detected, so this document was quarantined "
            "for human review before indexing. An editor can review and approve or discard "
            "it on the dashboard Documents page."
        )
    await record_audit(
        db,
        ctx,
        audit_action,
        resource_type="document",
        resource_id=document.id,
        meta=audit_meta,
    )
    logger.info("mcp_tool", tool="add_knowledge", org_id=str(ctx.org_id), status="quarantined")
    return {
        "id": str(document.id),
        "collection_id": str(collection.id),
        "title": document.title,
        "status": "quarantined",
        "chunk_count": 0,
        "created": True,
        "indexed": False,
        "findings": [
            {
                "detector": f.detector,
                "label": f.label,
                "severity": f.severity,
                "occurrences": f.occurrences,
            }
            for f in findings_src
        ],
        "message": message,
    }


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
async def _search_knowledge(
    db: AsyncSession, ctx: AuthContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _require_search_scope(ctx)
    query = _require_str(arguments, "query")

    top_k = arguments.get("top_k")
    if top_k is None:
        top_k = settings.RETRIEVAL_TOP_K
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise ToolError("'top_k' must be an integer")
    top_k = max(1, min(top_k, _MAX_TOP_K))

    collection_ids: list[uuid.UUID] | None = None
    collection_ref = arguments.get("collection")
    if isinstance(collection_ref, str) and collection_ref.strip():
        # Resolve the reference (id/slug/name) within the caller's org. Whether the caller
        # may actually see the collection is enforced by the retrieval scope below: a
        # collection the caller cannot view is dropped, so it simply yields no hits (parity
        # with REST ``/search``), never a chunk the caller is not entitled to.
        collection = await _resolve_collection(db, ctx, collection_ref)
        collection_ids = [collection.id]

    started = time.perf_counter()
    # Delegate to the shared retrieval spine: it resolves the caller's visibility scope
    # once, embeds the query through the org's connector with a Redis cache (metered as
    # EMBEDDING on a miss), and fuses vector + keyword hits - identical to REST ``/search``.
    hits = await retrieve(db, ctx, query, top_k=top_k, collection_ids=collection_ids)
    duration_ms = round((time.perf_counter() - started) * 1000)

    results = [
        {
            "document_id": str(h.document_id),
            "document_title": h.document_title,
            "collection_id": str(h.collection_id),
            "chunk_index": h.chunk_index,
            "score": round(float(h.score), 6),
            "snippet": (h.content or "")[:_SNIPPET_CHARS],
        }
        for h in hits
    ]

    # ``retrieve`` already meters the embedding as EMBEDDING usage; record the search as a
    # single SEARCH unit (no provider cost) so an Ask is never double-billed - same split
    # REST ``/search`` uses.
    await record_usage(
        db,
        ctx,
        UsageKind.SEARCH,
        units=1,
        latency_ms=duration_ms,
        meta={"query": query[:256], "results": len(results), "via": "mcp"},
    )
    await record_audit(
        db,
        ctx,
        AuditAction.SEARCH_PERFORMED.value,
        resource_type="search",
        meta={"query": query[:256], "results": len(results), "via": "mcp"},
    )
    logger.info(
        "mcp_tool",
        tool="search_knowledge",
        org_id=str(ctx.org_id),
        duration_ms=duration_ms,
        result_count=len(results),
    )
    return {"query": query, "count": len(results), "results": results}


async def _get_document(
    db: AsyncSession, ctx: AuthContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _require_read_scope(ctx)
    document_id = _parse_uuid(_require_str(arguments, "document_id"), "document_id")

    document = await db.get(Document, document_id)
    if document is None or document.org_id != ctx.org_id:
        raise ToolError("Document not found")

    perm = await effective_permission(db, ctx, ResourceType.DOCUMENT, document.id)
    if not permission_at_least(perm, PermissionLevel.VIEWER):
        raise ToolError("You do not have permission to read this document")

    content = ""
    if document.status != DocumentStatus.QUARANTINED:
        # A quarantined document's chunks (left over from a previously indexed version)
        # are withheld until it is approved, matching the search-side INDEXED-only guard.
        chunks = (
            (
                await db.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document.id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        content = "\n\n".join(c.content for c in chunks)

    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_ACCESSED.value,
        resource_type="document",
        resource_id=document.id,
        meta={"via": "mcp"},
    )
    return {
        "id": str(document.id),
        "collection_id": str(document.collection_id),
        "title": document.title,
        "status": document.status.value,
        "source_type": document.source_type.value,
        "source_uri": document.source_uri,
        "chunk_count": document.chunk_count,
        "size_bytes": document.size_bytes,
        "created_at": _iso(document.created_at),
        "indexed_at": _iso(document.indexed_at),
        "permission": perm.value,
        "content": content,
    }


async def _collection_grant_levels(
    db: AsyncSession, ctx: AuthContext, team_ids: set[uuid.UUID]
) -> dict[uuid.UUID, PermissionLevel]:
    """Best explicit collection grant per collection for the caller, in a single query.

    Batches what :func:`app.services.permissions.effective_permission` resolves one
    collection at a time, so listing every collection costs one grants query instead of one
    per collection. Mirrors that engine's principal set (the caller's user id plus their
    effective team ids).
    """
    principal_clauses = []
    if ctx.user_id is not None:
        principal_clauses.append(
            and_(
                AccessGrant.principal_type == PrincipalType.USER,
                AccessGrant.principal_id == ctx.user_id,
            )
        )
    if team_ids:
        principal_clauses.append(
            and_(
                AccessGrant.principal_type == PrincipalType.TEAM,
                AccessGrant.principal_id.in_(team_ids),
            )
        )
    if not principal_clauses:
        return {}
    rows = await db.execute(
        select(AccessGrant.resource_id, AccessGrant.permission).where(
            AccessGrant.org_id == ctx.org_id,
            AccessGrant.resource_type == ResourceType.COLLECTION,
            or_(*principal_clauses),
        )
    )
    best: dict[uuid.UUID, PermissionLevel] = {}
    for resource_id, permission in rows.all():
        best[resource_id] = max_permission(best.get(resource_id, PermissionLevel.NONE), permission)
    return best


def _grade_collection(
    ctx: AuthContext,
    collection: Collection,
    team_ids: set[uuid.UUID],
    grant_levels: dict[uuid.UUID, PermissionLevel],
) -> PermissionLevel:
    """Effective permission on ``collection`` computed in memory.

    Mirrors :func:`app.services.permissions.effective_permission` for a collection (the
    single source of truth): the MAXIMUM of admin/owner (MANAGER), the visibility baseline
    (``default_permission`` when the caller is reached by the collection's visibility), and
    the best explicit grant - so this listing can never disagree with route authorization.
    """
    if ctx.is_admin:
        return PermissionLevel.MANAGER
    levels = [PermissionLevel.NONE, grant_levels.get(collection.id, PermissionLevel.NONE)]
    if ctx.user_id is not None and collection.owner_id == ctx.user_id:
        levels.append(PermissionLevel.MANAGER)
    if _visibility_grants_access(collection.visibility, ctx, collection.owner_team_id, team_ids):
        levels.append(collection.default_permission)
    return max_permission(*levels)


async def _list_collections(
    db: AsyncSession, ctx: AuthContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _require_read_scope(ctx)
    collections = (
        (
            await db.execute(
                select(Collection).where(Collection.org_id == ctx.org_id).order_by(Collection.name)
            )
        )
        .scalars()
        .all()
    )

    # Grade every collection in one pass. Resolve the caller's team set and grants a single
    # time up front and reuse them for each collection, instead of an O(N) permission query
    # per collection. Admins are MANAGER everywhere, so their team/grant lookups are skipped.
    team_ids: set[uuid.UUID] = set()
    grant_levels: dict[uuid.UUID, PermissionLevel] = {}
    if not ctx.is_admin:
        team_ids = await user_team_ids(db, ctx)
        grant_levels = await _collection_grant_levels(db, ctx, team_ids)

    visible: list[dict[str, Any]] = []
    for collection in collections:
        perm = _grade_collection(ctx, collection, team_ids, grant_levels)
        if perm == PermissionLevel.NONE:
            continue
        visible.append(
            {
                "id": str(collection.id),
                "name": collection.name,
                "slug": collection.slug,
                "description": collection.description,
                "visibility": collection.visibility.value,
                "document_count": collection.document_count,
                "permission": perm.value,
            }
        )
    return {"count": len(visible), "collections": visible}


# --------------------------------------------------------------------------- #
# Auto-routing: file a capture into the caller's best-matching editable collection
# --------------------------------------------------------------------------- #
async def _editable_collections(db: AsyncSession, ctx: AuthContext) -> list[Collection]:
    """Every collection in the caller's org they can write to (EDITOR+), graded in one pass.

    Reuses the same grading as ``list_collections`` so auto-routing can never target a
    collection the caller is not actually allowed to write to.
    """
    collections = (
        (
            await db.execute(
                select(Collection).where(Collection.org_id == ctx.org_id).order_by(Collection.name)
            )
        )
        .scalars()
        .all()
    )
    team_ids: set[uuid.UUID] = set()
    grant_levels: dict[uuid.UUID, PermissionLevel] = {}
    if not ctx.is_admin:
        team_ids = await user_team_ids(db, ctx)
        grant_levels = await _collection_grant_levels(db, ctx, team_ids)
    return [
        c
        for c in collections
        if permission_at_least(
            _grade_collection(ctx, c, team_ids, grant_levels), PermissionLevel.EDITOR
        )
    ]


def _parse_choice(text: str, n: int) -> int | None:
    """Parse the classifier's reply as a 1..n choice (``0`` / anything else -> ``None``)."""
    token = text.strip().split()[0].strip(".,:)") if text.strip() else ""
    try:
        value = int(token)
    except ValueError:
        return None
    return value if 1 <= value <= n else None


async def _classify_collection(
    db: AsyncSession,
    ctx: AuthContext,
    title: str,
    content: str,
    candidates: list[Collection],
) -> Collection | None:
    """Ask the org's completion model which candidate collection best fits the document.

    Returns the chosen collection, or ``None`` when the model abstains, its reply can't be
    parsed, or no completion provider is configured (the offline stub) - the caller then
    falls back to a deterministic heuristic. Never raises: routing must not break a capture.
    """
    if not candidates:
        return None
    if len(candidates) > _MAX_CLASSIFY_CANDIDATES:
        # Bound the prompt: offer only the most-populated collections as candidates.
        candidates = sorted(candidates, key=lambda c: c.document_count, reverse=True)[
            :_MAX_CLASSIFY_CANDIDATES
        ]
    listing = "\n".join(
        f"{i + 1}. {c.name} - {(c.description or '').strip()}" for i, c in enumerate(candidates)
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You file a new knowledge document into the single most relevant collection. "
                "Reply with ONLY the number of the best-matching collection, or 0 if none fit."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Collections:\n{listing}\n\nDocument title: {title}\n"
                f"Document excerpt:\n{content[:_CLASSIFY_CONTENT_CHARS]}\n\n"
                f"Answer with just the number (1-{len(candidates)}), or 0."
            ),
        ),
    ]
    try:
        comp = await resolver.resolve(db, ctx.org_id, ConnectorPurpose.COMPLETION)
        result = await complete(
            messages,
            model=comp.model,
            api_key=comp.api_key,
            api_base=comp.api_base,
            provider=comp.provider,
            temperature=0.0,
            # On the Claude 5 family ``max_tokens`` caps default-on thinking PLUS the
            # visible answer; a tiny cap yields an empty reply (thinking eats it all).
            max_tokens=1024,
        )
    except Exception as exc:  # never let routing break a capture
        logger.warning("auto_route_classify_failed", error=type(exc).__name__)
        return None
    if is_billable_provider(result.provider):
        await record_usage(
            db,
            ctx,
            UsageKind.COMPLETION,
            provider=result.provider,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            units=1,
            cost_usd=completion_cost(result.model, result.tokens_in, result.tokens_out),
            meta={"via": "mcp", "op": "auto_route"},
        )
    choice = _parse_choice(result.text, len(candidates))
    return candidates[choice - 1] if choice else None


async def _get_or_create_decisions_collection(db: AsyncSession, ctx: AuthContext) -> Collection:
    """The shared 'Decisions' collection - auto-capture's home when nothing else fits.

    Reused if it already exists (unique per org by slug); otherwise created org-visible and
    org-writable so any teammate's agent can add to and read the decisions log. Creation
    mirrors REST's ``POST /collections``: it requires a user-bound caller with an org role
    of editor or higher, and is recorded in the audit log.
    """
    lookup = select(Collection).where(
        Collection.org_id == ctx.org_id, Collection.slug == _DECISIONS_SLUG
    )
    existing = (await db.execute(lookup)).scalar_one_or_none()
    if existing is not None:
        return existing
    if ctx.user_id is None or not role_at_least(ctx.org_role, OrgRole.EDITOR):
        raise ToolError(
            "No collection to file this in. Pass an explicit 'collection', or ask an admin "
            "to create one."
        )
    collection = Collection(
        org_id=ctx.org_id,
        owner_id=ctx.user_id,
        name=_DECISIONS_NAME,
        slug=_DECISIONS_SLUG,
        description="Decisions and notes captured automatically by agents as they work.",
        visibility=Visibility.ORG,
        default_permission=PermissionLevel.EDITOR,
    )
    try:
        # Two agents can race the first capture in an org; the slug is unique per org, so
        # the loser falls back to the winner's row instead of failing the capture.
        async with db.begin_nested():
            db.add(collection)
            await db.flush()
    except IntegrityError:
        return (await db.execute(lookup)).scalar_one()
    await record_audit(
        db,
        ctx,
        "collection.created",
        resource_type=ResourceType.COLLECTION.value,
        resource_id=collection.id,
        meta={"name": collection.name, "via": "mcp", "auto_created": True},
    )
    return collection


async def _auto_route_collection(
    db: AsyncSession, ctx: AuthContext, title: str, content: str, *, allow_classifier: bool
) -> Collection:
    """Pick the collection a capture with no explicit ``collection`` should land in.

    With exactly one editable collection the answer is fixed, so no classifier runs.
    Otherwise the classifier chooses among the caller's editable collections; on an
    abstain/offline it falls back to an editable 'Decisions' collection, then the
    most-populated editable one. With no editable collection at all, the shared 'Decisions'
    collection is created (or reused) as the home. ``allow_classifier=False`` keeps content
    that failed a secret/DLP scan out of the completion provider's prompt: routing then
    stays fully deterministic.
    """
    editable = await _editable_collections(db, ctx)
    if len(editable) == 1:
        return editable[0]
    if editable:
        if allow_classifier:
            chosen = await _classify_collection(db, ctx, title, content, editable)
            if chosen is not None:
                return chosen
        for c in editable:  # deterministic fallback: an existing decisions log, if any
            if c.slug == _DECISIONS_SLUG or c.name.strip().lower() == _DECISIONS_NAME.lower():
                return c
        return max(editable, key=lambda c: c.document_count)
    collection = await _get_or_create_decisions_collection(db, ctx)
    # An org may already have a 'decisions' collection this caller cannot write to; surface
    # a clear error instead of a permission failure naming a collection they never chose.
    if not permission_at_least(
        await effective_permission(db, ctx, ResourceType.COLLECTION, collection.id),
        PermissionLevel.EDITOR,
    ):
        raise ToolError(
            "No editable collection to file this in. Pass an explicit 'collection', or ask "
            "an admin for editor access."
        )
    return collection


async def _add_knowledge(
    db: AsyncSession, ctx: AuthContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _require_write_scope(ctx)
    title = _require_str(arguments, "title")
    content = _require_str(arguments, "content")
    if len(content) > MAX_TEXT_CONTENT_CHARS:
        raise ToolError(f"'content' exceeds the {MAX_TEXT_CONTENT_CHARS} character limit")
    doc_type = arguments.get("doc_type")
    if doc_type is not None and (not isinstance(doc_type, str) or doc_type not in _DOC_TYPES):
        raise ToolError(f"'doc_type' must be one of: {', '.join(sorted(_DOC_TYPES))}")

    collection_ref = arguments.get("collection")
    if collection_ref is not None and (
        not isinstance(collection_ref, str) or not collection_ref.strip()
    ):
        raise ToolError("'collection' must be a non-empty string (an id, slug, or name)")

    # Scan BEFORE routing: auto-routing may send a content excerpt to the completion
    # provider, and content the scanner would quarantine must never leave the box. Scanning
    # is CPU-bound, so offload it off the event loop like the ingestion worker and
    # ``chunk_text`` do.
    scan_report: ScanReport | None = None
    if settings.SECRET_SCAN_ENABLED:
        scan_report = await asyncio.to_thread(scan_text, content)
    dlp_report = None
    if settings.DLP_ENABLED:
        dlp_report = await asyncio.to_thread(dlp_scan_text, content)
    flagged = bool(scan_report and scan_report.flagged) or bool(dlp_report and dlp_report.flagged)

    # The collection is optional: when a proactively-capturing agent omits it, route the
    # document to its best-matching editable collection automatically.
    auto_routed = collection_ref is None
    if auto_routed:
        collection = await _auto_route_collection(
            db, ctx, title, content, allow_classifier=not flagged
        )
    else:
        collection = await _resolve_collection(db, ctx, collection_ref)
    await require_permission(
        db, ctx, ResourceType.COLLECTION, collection.id, PermissionLevel.EDITOR
    )

    # Quarantine gate (mirrors the worker's): flagged content is persisted for review
    # but never chunked, embedded, or made searchable.
    if scan_report is not None and scan_report.flagged:
        return await _quarantine_new_document(db, ctx, collection, title, content, scan_report)

    # DLP/PII pass (parity with the ingestion worker's second gate): quarantine when the
    # deployment's action is 'quarantine', otherwise label the document's sensitivity
    # ('warn' records the finding without labelling). MCP previously skipped this entirely.
    dlp_sensitivity = None
    dlp_meta: dict | None = None
    if dlp_report is not None and dlp_report.flagged:
        if settings.DLP_DEFAULT_ACTION == "quarantine":
            return await _quarantine_new_document(
                db, ctx, collection, title, content, dlp_report=dlp_report
            )
        dlp_meta = dlp_report_to_meta(dlp_report)
        if settings.DLP_DEFAULT_ACTION != "warn":
            dlp_sensitivity = dlp_report.sensitivity

    meta: dict[str, Any] = {"via": "mcp"}
    if doc_type is not None:
        meta["doc_type"] = doc_type
    if auto_routed:
        meta["auto_collection"] = True
    if dlp_meta:
        meta[DLP_META_KEY] = dlp_meta
    document = Document(
        org_id=ctx.org_id,
        collection_id=collection.id,
        created_by_id=ctx.user_id,
        title=title[:1024],
        source_type=SourceType.TEXT,
        status=DocumentStatus.PROCESSING,
        size_bytes=len(content.encode("utf-8")),
        meta=meta,
    )
    if dlp_sensitivity is not None:
        document.sensitivity = dlp_sensitivity
    db.add(document)
    await db.flush()

    started = time.perf_counter()
    chunk_count = await _index_document(db, ctx, document, content)
    duration_ms = round((time.perf_counter() - started) * 1000)
    # Atomic increment so concurrent writes to the same collection cannot lose updates.
    collection.document_count = Collection.document_count + 1

    await record_audit(
        db,
        ctx,
        AuditAction.DOCUMENT_CREATED.value,
        resource_type="document",
        resource_id=document.id,
        meta={"collection_id": str(collection.id), "via": "mcp"},
    )
    logger.info(
        "mcp_tool",
        tool="add_knowledge",
        org_id=str(ctx.org_id),
        duration_ms=duration_ms,
        chunk_count=chunk_count,
    )
    return {
        "id": str(document.id),
        "collection_id": str(collection.id),
        "collection": collection.name,
        "title": document.title,
        "status": document.status.value,
        "chunk_count": chunk_count,
        "created": True,
        "auto_routed": auto_routed,
    }


async def _update_knowledge(
    db: AsyncSession, ctx: AuthContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _require_write_scope(ctx)
    document_id = _parse_uuid(_require_str(arguments, "document_id"), "document_id")
    content = _require_str(arguments, "content")
    if len(content) > MAX_TEXT_CONTENT_CHARS:
        raise ToolError(f"'content' exceeds the {MAX_TEXT_CONTENT_CHARS} character limit")

    document = await db.get(Document, document_id)
    if document is None or document.org_id != ctx.org_id:
        raise ToolError("Document not found")

    await require_permission(db, ctx, ResourceType.DOCUMENT, document.id, PermissionLevel.EDITOR)

    # Scan BEFORE any mutation: a flagged update is rejected outright (the MCP flow has
    # no human in the loop to review it) and the stored document stays untouched. Scanning
    # is CPU-bound, so offload it off the event loop like the ingestion worker does.
    if settings.SECRET_SCAN_ENABLED:
        report = await asyncio.to_thread(scan_text, content)
        if report.flagged:
            labels = ", ".join(sorted({f.label for f in report.findings}))
            raise ToolError(
                f"Update rejected: the new content appears to contain secrets ({labels}). "
                "The document was left unchanged. Remove the secrets and try again, or "
                "upload it via the dashboard where it can be reviewed and approved."
            )

    # DLP/PII pass (parity with add_knowledge + the worker). No human is in the MCP loop, so
    # under a 'quarantine' policy a flagged update is rejected outright; otherwise the new
    # content's sensitivity is (re)labelled below.
    dlp_report = await asyncio.to_thread(dlp_scan_text, content) if settings.DLP_ENABLED else None
    if (
        dlp_report is not None
        and dlp_report.flagged
        and settings.DLP_DEFAULT_ACTION == "quarantine"
    ):
        raise ToolError(
            "Update rejected: the new content contains personal or confidential data and "
            "this deployment quarantines such content for review. Upload it via the "
            "dashboard where it can be reviewed and approved."
        )

    started = time.perf_counter()
    chunk_count = await _index_document(db, ctx, document, content, replace=True)
    duration_ms = round((time.perf_counter() - started) * 1000)

    # Re-label the document's sensitivity to match the NEW content (clear it when clean).
    if dlp_report is not None and dlp_report.flagged:
        if settings.DLP_DEFAULT_ACTION != "warn":
            document.sensitivity = dlp_report.sensitivity
        document.meta = {**(document.meta or {}), DLP_META_KEY: dlp_report_to_meta(dlp_report)}
    elif dlp_report is not None:
        document.sensitivity = SensitivityLevel.NONE
        if document.meta and DLP_META_KEY in document.meta:
            document.meta = {k: v for k, v in document.meta.items() if k != DLP_META_KEY}

    # Replacing the content invalidates any prior verification (parity with the REST answer
    # edit path): a "verified" badge must not survive a content rewrite.
    document.verification_status = VerificationStatus.UNVERIFIED
    document.verified_by_id = None
    document.verified_at = None
    document.expires_at = None

    await record_audit(
        db,
        ctx,
        "document.updated",
        resource_type="document",
        resource_id=document.id,
        meta={"via": "mcp"},
    )
    logger.info(
        "mcp_tool",
        tool="update_knowledge",
        org_id=str(ctx.org_id),
        duration_ms=duration_ms,
        chunk_count=chunk_count,
    )
    return {
        "id": str(document.id),
        "collection_id": str(document.collection_id),
        "title": document.title,
        "status": document.status.value,
        "chunk_count": chunk_count,
        "updated": True,
    }


_DISPATCH = {
    "search_knowledge": _search_knowledge,
    "get_document": _get_document,
    "list_collections": _list_collections,
    "add_knowledge": _add_knowledge,
    "update_knowledge": _update_knowledge,
}


async def dispatch_tool(
    db: AsyncSession,
    ctx: AuthContext,
    name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the named tool. Raises :class:`ToolError` for user-facing failures."""
    handler = _DISPATCH.get(name)
    if handler is None:
        raise ToolError(f"Unknown tool: {name!r}")
    return await handler(db, ctx, arguments or {})
