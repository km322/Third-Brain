"""Knowledge data-source connectors and their identity/ACL mapping.

A :class:`DataSource` pulls documents from an external system (Google Drive, Slack,
GitHub, a local folder, …) into a target collection. Crucially it also pulls each
object's **source-system ACL**; those principals are mapped to Third Brain users/teams
(:class:`ExternalIdentity`) and materialised as ordinary ``AccessGrant`` rows, so the
single permission engine enforces connector permissions with no special-casing.

The raw source ACL is preserved per document (:class:`DocumentExternalPrincipal`) so
that grants can be *backfilled* when an external principal is mapped after the fact
(e.g. once SCIM/SSO provisions the user).

This is deliberately separate from :class:`app.models.connector.Connector`, which
configures LLM *endpoints* - a different concept that happens to share the word.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    DataSourceKind,
    DataSourceStatus,
    ExternalPrincipalKind,
    Visibility,
)

if TYPE_CHECKING:
    pass


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A configured external knowledge source that syncs documents + ACLs into a collection."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_data_source_org_name"),
        Index("ix_data_sources_org_status", "org_id", "status"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[DataSourceKind] = mapped_column(
        Enum(DataSourceKind, native_enum=False, length=32), nullable=False
    )
    status: Mapped[DataSourceStatus] = mapped_column(
        Enum(DataSourceStatus, native_enum=False, length=32),
        default=DataSourceStatus.ACTIVE,
        nullable=False,
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    """Non-secret provider options (root path, repo, channel id, base url, …)."""
    encrypted_secret: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    """Fernet-encrypted OAuth token / API secret (see ``app.core.security.encrypt_secret``)."""
    cursor: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    """Opaque incremental-sync cursor (page token / commit sha / change id)."""
    default_visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False, length=32),
        default=Visibility.PRIVATE,
        nullable=False,
    )
    """Baseline visibility for synced documents. PRIVATE means "only the synced ACL grants
    confer access", which is the safe default for permission-mirrored ingestion."""
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Scheduled cadence in minutes; NULL disables scheduling (manual "sync now" only)."""
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def provider(self) -> str:
        """Identity namespace for this source's principals (matches ExternalIdentity.provider)."""
        return self.kind.value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DataSource {self.name!r} kind={self.kind} status={self.status}>"


class ExternalIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Maps a source-system principal (user or group) to an internal user or team.

    Exactly one of ``user_id`` / ``team_id`` is set. Used to translate source ACLs into
    ``AccessGrant`` rows. Email-form principals are also auto-matched to org members at
    sync time; explicit rows here cover non-email principals (group ids, workspace ids).
    """

    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "external_id", name="uq_external_identity"),
        Index("ix_external_identity_lookup", "provider", "external_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[ExternalPrincipalKind] = mapped_column(
        Enum(ExternalPrincipalKind, native_enum=False, length=16),
        default=ExternalPrincipalKind.USER,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExternalIdentity {self.provider}:{self.external_id}>"


class DocumentExternalPrincipal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The raw source-system ACL of a synced document: who (externally) may read it.

    Preserved so grants can be backfilled when a principal is mapped later, and so a
    re-sync can diff the ACL. One row per (document, provider, external principal).
    """

    __tablename__ = "document_external_principals"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "provider", "external_id", name="uq_doc_external_principal"
        ),
        Index("ix_doc_ext_principal_lookup", "provider", "external_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[ExternalPrincipalKind] = mapped_column(
        Enum(ExternalPrincipalKind, native_enum=False, length=16),
        default=ExternalPrincipalKind.USER,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocumentExternalPrincipal {self.provider}:{self.external_id}>"
