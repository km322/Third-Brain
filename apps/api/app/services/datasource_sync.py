"""Data-source sync engine: pull documents + source ACLs into Third Brain.

For each document a connector returns, this:
1. Upserts a ``Document`` (natural key = ``(source_id, external_id)``) and stores its bytes.
2. Runs the SAME ingestion pipeline as manual uploads (extract → scan → chunk → embed),
   so secret quarantine and everything else applies uniformly.
3. Mirrors the source-system ACL into ``AccessGrant`` rows (scoped by ``source_id`` so a
   re-sync never touches manual grants), resolving each external principal to a Third Brain
   user/team via :class:`ExternalIdentity` or email auto-match. Unmapped principals are
   preserved as :class:`DocumentExternalPrincipal` rows and BACKFILLED into grants the
   moment the principal is mapped (:func:`map_identity`).

Because permissions are ordinary ``AccessGrant`` rows, the permission engine enforces
connector ACLs with zero special-casing - a chunk the caller's source access does not
cover can never enter a search result.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.models.access import AccessGrant
from app.models.collection import Collection
from app.models.datasource import DataSource, DocumentExternalPrincipal, ExternalIdentity
from app.models.document import Document
from app.models.enums import (
    AuditAction,
    DataSourceStatus,
    DocumentStatus,
    ExternalPrincipalKind,
    MembershipStatus,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    SourceType,
)
from app.models.user import Membership, User
from app.services.connectors import RemoteDocument, RemotePrincipal, get_connector
from app.services.ingestion import ingest_document
from app.services.metering import record_audit
from app.services.storage import get_storage

logger = get_logger(__name__)


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    missing: bool = False

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
        }


def _resolved_principal(ident: ExternalIdentity) -> tuple[PrincipalType, uuid.UUID] | None:
    if ident.user_id is not None:
        return (PrincipalType.USER, ident.user_id)
    if ident.team_id is not None:
        return (PrincipalType.TEAM, ident.team_id)
    return None


async def _find_user_by_email(db: AsyncSession, org_id: uuid.UUID, email: str) -> uuid.UUID | None:
    """Find an ACTIVE org member by email (case-insensitive) for principal auto-mapping.

    ACTIVE only, matching the manual-mapping route: an INVITED (not-yet-joined) member cannot
    authenticate into the org, so materializing source ACL grants for them would contradict the
    invariant that an invited membership confers no access.
    """
    return (
        await db.execute(
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.status == MembershipStatus.ACTIVE,
                func.lower(User.email) == email.strip().lower(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_principal(
    db: AsyncSession, source: DataSource, principal: RemotePrincipal
) -> tuple[PrincipalType, uuid.UUID] | None:
    """Resolve a source-system principal to a Third Brain (user|team) grantee, or None."""
    ident = (
        await db.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.org_id == source.org_id,
                ExternalIdentity.provider == source.provider,
                ExternalIdentity.external_id == principal.external_id,
            )
        )
    ).scalar_one_or_none()
    if ident is not None:
        return _resolved_principal(ident)
    # Auto-map a USER principal whose id is an email to a matching org member.
    if principal.kind == ExternalPrincipalKind.USER and "@" in principal.external_id:
        user_id = await _find_user_by_email(db, source.org_id, principal.external_id)
        if user_id is not None:
            return (PrincipalType.USER, user_id)
    return None


async def _sync_document_acl(
    db: AsyncSession, source: DataSource, doc: Document, acl: list[RemotePrincipal]
) -> None:
    """Replace this source's grants for ``doc`` with grants derived from ``acl``.

    Records the raw source principals (for later backfill) and only materialises grants for
    principals that resolve to a user/team. Never adds a grant that duplicates a manual one.
    """
    existing = (
        (
            await db.execute(
                select(AccessGrant).where(
                    AccessGrant.org_id == source.org_id,
                    AccessGrant.resource_type == ResourceType.DOCUMENT,
                    AccessGrant.resource_id == doc.id,
                )
            )
        )
        .scalars()
        .all()
    )
    manual = {(g.principal_type, g.principal_id) for g in existing if g.source_id is None}

    # Clear this source's prior grants + principal records for the doc, then rebuild.
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.resource_type == ResourceType.DOCUMENT,
            AccessGrant.resource_id == doc.id,
            AccessGrant.source_id == source.id,
        )
    )
    await db.execute(
        delete(DocumentExternalPrincipal).where(
            DocumentExternalPrincipal.document_id == doc.id,
            DocumentExternalPrincipal.source_id == source.id,
        )
    )

    added: set[tuple[PrincipalType, uuid.UUID]] = set()
    for principal in acl:
        db.add(
            DocumentExternalPrincipal(
                org_id=source.org_id,
                document_id=doc.id,
                source_id=source.id,
                provider=source.provider,
                external_id=principal.external_id,
                kind=principal.kind,
            )
        )
        resolved = await _resolve_principal(db, source, principal)
        if resolved is None or resolved in manual or resolved in added:
            continue
        ptype, pid = resolved
        db.add(
            AccessGrant(
                org_id=source.org_id,
                resource_type=ResourceType.DOCUMENT,
                resource_id=doc.id,
                principal_type=ptype,
                principal_id=pid,
                permission=PermissionLevel.VIEWER,
                source_id=source.id,
            )
        )
        added.add(resolved)


async def _upsert_document(
    db: AsyncSession, source: DataSource, rdoc: RemoteDocument
) -> tuple[Document, bool, bool]:
    """Create or update the Document for ``rdoc``. Returns (document, created, changed)."""
    content = rdoc.content
    checksum = hashlib.sha256(content).hexdigest()
    filename = rdoc.external_id.rsplit("/", 1)[-1] or rdoc.title
    existing = (
        await db.execute(
            select(Document).where(
                Document.source_id == source.id,
                Document.external_id == rdoc.external_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Unchanged and already indexed: keep the index, refresh only light metadata; the
        # caller still re-syncs the ACL (source permissions may have changed independently).
        if existing.checksum == checksum and existing.status == DocumentStatus.INDEXED:
            existing.title = rdoc.title[:1024]
            existing.source_uri = rdoc.source_uri
            await db.commit()
            return existing, False, False
        existing.title = rdoc.title[:1024]
        existing.mime_type = rdoc.mime_type
        existing.source_uri = rdoc.source_uri
        existing.size_bytes = len(content)
        existing.checksum = checksum
        existing.status = DocumentStatus.PENDING
        existing.error = None
        key = existing.storage_key or _storage_key(source.org_id, existing.id, filename)
        await get_storage().save(key, content, rdoc.mime_type)
        existing.storage_key = key
        await db.commit()
        return existing, False, True

    doc = Document(
        org_id=source.org_id,
        collection_id=source.collection_id,
        created_by_id=source.created_by_id,
        title=rdoc.title[:1024],
        source_type=SourceType.CONNECTOR,
        source_uri=rdoc.source_uri,
        mime_type=rdoc.mime_type,
        visibility=source.default_visibility,
        status=DocumentStatus.PENDING,
        size_bytes=len(content),
        checksum=checksum,
        source_id=source.id,
        external_id=rdoc.external_id,
    )
    db.add(doc)
    await db.flush()  # assign id for the storage key
    key = _storage_key(source.org_id, doc.id, filename)
    await get_storage().save(key, content, rdoc.mime_type)
    doc.storage_key = key
    # Keep the collection's document_count in step with the manual upload routes (atomic
    # expression so a concurrent sync/upload can't lose the update).
    await db.execute(
        update(Collection)
        .where(Collection.id == source.collection_id)
        .values(document_count=Collection.document_count + 1)
    )
    await db.commit()
    return doc, True, True


def _storage_key(org_id: uuid.UUID, document_id: uuid.UUID, filename: str | None) -> str:
    from app.services.storage import build_storage_key

    return build_storage_key(org_id, document_id, filename)


async def _delete_doc(db: AsyncSession, doc: Document) -> None:
    """Delete a synced document, its blob and its (FK-less) document-level grants."""
    if doc.storage_key:
        try:
            await get_storage().delete(doc.storage_key)
        except Exception:  # pragma: no cover - blob cleanup is best-effort
            pass
    # AccessGrants have no FK to documents, so remove them explicitly (chunks +
    # external-principal rows cascade via their FKs).
    await db.execute(
        delete(AccessGrant).where(
            AccessGrant.resource_type == ResourceType.DOCUMENT,
            AccessGrant.resource_id == doc.id,
        )
    )
    if doc.collection_id is not None:
        await db.execute(
            update(Collection)
            .where(Collection.id == doc.collection_id)
            .values(document_count=Collection.document_count - 1)
        )
    await db.delete(doc)


async def _prune_missing(db: AsyncSession, source: DataSource, seen: set[str]) -> int:
    """Delete documents previously synced from ``source`` that are gone upstream."""
    docs = (
        (await db.execute(select(Document).where(Document.source_id == source.id))).scalars().all()
    )
    removed = 0
    for doc in docs:
        if doc.external_id in seen:
            continue
        await _delete_doc(db, doc)
        removed += 1
    if removed:
        await db.commit()
    return removed


async def sync_data_source(db: AsyncSession, source_id: uuid.UUID) -> SyncStats:
    """Run a full sync for a data source. Marks status transitions and records an audit.

    Raises :class:`ConnectorError` (mapped to 4xx by the route) on connector failures,
    after flipping the source to ERROR with a message.
    """
    source = await db.get(DataSource, source_id)
    if source is None:
        return SyncStats(missing=True)

    source.status = DataSourceStatus.SYNCING
    await db.commit()
    stats = SyncStats()
    try:
        connector = get_connector(source)
        secret = decrypt_secret(source.encrypted_secret) if source.encrypted_secret else None
        batch = await connector.fetch(source.cursor, secret)

        seen: set[str] = set()
        for rdoc in batch.documents:
            if rdoc.deleted:
                doc = (
                    await db.execute(
                        select(Document).where(
                            Document.source_id == source.id,
                            Document.external_id == rdoc.external_id,
                        )
                    )
                ).scalar_one_or_none()
                if doc is not None:
                    await _delete_doc(db, doc)
                    await db.commit()
                    stats.deleted += 1
                continue
            seen.add(rdoc.external_id)
            doc, created, changed = await _upsert_document(db, source, rdoc)
            if changed:
                await ingest_document(db, doc.id)
            await _sync_document_acl(db, source, doc, rdoc.acl)
            await db.commit()
            if created:
                stats.created += 1
            elif changed:
                stats.updated += 1
            else:
                stats.skipped += 1

        if batch.full_sync:
            stats.deleted += await _prune_missing(db, source, seen)

        source = await db.get(DataSource, source_id)
        source.cursor = batch.cursor
        source.last_synced_at = datetime.now(UTC)
        source.last_error = None
        source.status = DataSourceStatus.ACTIVE
        source.document_count = (
            await db.execute(
                select(func.count()).select_from(Document).where(Document.source_id == source.id)
            )
        ).scalar_one()
        await record_audit(
            db,
            None,
            AuditAction.DATA_SOURCE_SYNCED.value,
            org_id=source.org_id,
            resource_type="data_source",
            resource_id=str(source.id),
            meta=stats.as_dict(),
        )
        await db.commit()
        logger.info("data_source_synced", data_source_id=str(source.id), **stats.as_dict())
        return stats
    except BaseException as exc:
        # BaseException (not just Exception) so that asyncio.CancelledError - raised on the arq
        # job_timeout, a graceful worker shutdown, or a client disconnect on the inline route -
        # ALSO flips the source out of SYNCING. Otherwise a cancelled sync stays SYNCING forever
        # and the due-sync cron (which only picks ACTIVE sources) never schedules it again; a
        # hard kill that skips even this handler is caught by reap_stuck_data_sources. We flip to
        # ERROR (visible + retryable) and re-raise, so SystemExit/KeyboardInterrupt still bubble.
        await db.rollback()
        source = await db.get(DataSource, source_id)
        if source is not None:
            source.status = DataSourceStatus.ERROR
            source.last_error = (str(exc) or type(exc).__name__)[:2000]
            await db.commit()
        logger.warning(
            "data_source_sync_failed",
            data_source_id=str(source_id),
            error=str(exc) or type(exc).__name__,
        )
        raise


# A data source stuck in SYNCING longer than this was almost certainly stranded by a hard
# worker death (OOM/SIGKILL) or a redelivery gap that skipped the in-band handler. Well above
# the 600s arq job_timeout so a legitimately long sync is never reaped mid-flight.
STUCK_SYNC_TIMEOUT_SECONDS = 30 * 60


async def reap_stuck_data_sources(
    db: AsyncSession, *, older_than_seconds: int = STUCK_SYNC_TIMEOUT_SECONDS
) -> int:
    """Flip data sources stranded mid-sync (SYNCING) to ERROR so they are visible + reschedulable.

    A source whose sync process was hard-killed stays SYNCING forever, and the due-sync cron
    only picks ACTIVE sources, so it would never sync again and the UI shows a perpetual
    "syncing". This surfaces it as ERROR (an admin can re-trigger) after a generous timeout.
    The sibling of :func:`app.services.ingestion.reap_stuck_documents`. Returns the count reaped.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    result = await db.execute(
        update(DataSource)
        .where(
            DataSource.status == DataSourceStatus.SYNCING,
            DataSource.updated_at < cutoff,
        )
        .values(
            status=DataSourceStatus.ERROR,
            last_error="Sync did not complete (worker crash or timeout); re-trigger to retry.",
        )
    )
    await db.commit()
    reaped = result.rowcount or 0
    if reaped:
        logger.info("stuck_data_sources_reaped", count=reaped)
    return reaped


async def map_identity(
    db: AsyncSession,
    org_id: uuid.UUID,
    provider: str,
    external_id: str,
    kind: ExternalPrincipalKind,
    *,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> int:
    """Map a source principal to a user/team and BACKFILL grants for its documents.

    Returns the number of new grants created. Exactly one of ``user_id``/``team_id`` must be
    set. The caller commits.
    """
    if bool(user_id) == bool(team_id):
        raise ValueError("map_identity requires exactly one of user_id / team_id")

    ident = (
        await db.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.org_id == org_id,
                ExternalIdentity.provider == provider,
                ExternalIdentity.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if ident is None:
        ident = ExternalIdentity(
            org_id=org_id, provider=provider, external_id=external_id, kind=kind
        )
        db.add(ident)
    ident.kind = kind
    ident.user_id = user_id
    ident.team_id = team_id
    await db.flush()

    ptype = PrincipalType.USER if user_id else PrincipalType.TEAM
    pid = user_id or team_id

    principals = (
        (
            await db.execute(
                select(DocumentExternalPrincipal).where(
                    DocumentExternalPrincipal.org_id == org_id,
                    DocumentExternalPrincipal.provider == provider,
                    DocumentExternalPrincipal.external_id == external_id,
                )
            )
        )
        .scalars()
        .all()
    )
    backfilled = 0
    for p in principals:
        exists = (
            await db.execute(
                select(AccessGrant.id).where(
                    AccessGrant.resource_type == ResourceType.DOCUMENT,
                    AccessGrant.resource_id == p.document_id,
                    AccessGrant.principal_type == ptype,
                    AccessGrant.principal_id == pid,
                )
            )
        ).first()
        if exists is not None:
            continue
        db.add(
            AccessGrant(
                org_id=org_id,
                resource_type=ResourceType.DOCUMENT,
                resource_id=p.document_id,
                principal_type=ptype,
                principal_id=pid,
                permission=PermissionLevel.VIEWER,
                source_id=p.source_id,
            )
        )
        backfilled += 1
    return backfilled
