"""Enterprise identity: SSO connections, federated identities, SCIM tokens, invites.

- :class:`SsoConnection` - per-org OIDC or SAML IdP configuration.
- :class:`FederatedIdentity` - links an IdP subject/NameID to a Third Brain user.
- :class:`ScimToken` - bearer credential a provider (Okta/Azure AD) uses to provision
  users/groups via the SCIM 2.0 endpoints. Stored only as a SHA-256 hash.
- :class:`Invite` - a pending email invitation (replaces the old "invitee must already
  have an account" flow). Token stored only as a hash.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import InviteStatus, OrgRole, SsoProtocol

if TYPE_CHECKING:
    pass


class SsoConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-org OIDC or SAML identity-provider configuration."""

    __tablename__ = "sso_connections"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_sso_connection_org_name"),
        Index("ix_sso_connections_domain", "email_domain"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    protocol: Mapped[SsoProtocol] = mapped_column(
        Enum(SsoProtocol, native_enum=False, length=16), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Optional email domain for IdP discovery ("acme.com" -> route acme.com logins here)."""
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    """OIDC: issuer, authorization_endpoint, token_endpoint, jwks_uri, client_id, scopes.
    SAML: sp_entity_id, idp_entity_id, idp_sso_url, idp_x509_cert."""
    encrypted_secret: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    """Fernet-encrypted OIDC client_secret (SAML needs none)."""
    default_role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, native_enum=False, length=32),
        default=OrgRole.VIEWER,
        nullable=False,
    )
    """Role assigned to a user provisioned just-in-time on first SSO login."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SsoConnection {self.name!r} {self.protocol}>"


class FederatedIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Binds an IdP subject (OIDC ``sub`` / SAML NameID) to a Third Brain user."""

    __tablename__ = "federated_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_federated_identity"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sso_connections.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FederatedIdentity {self.provider}:{self.subject}>"


class ScimToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A bearer token an IdP uses to provision users/groups over SCIM 2.0.

    Only the SHA-256 hash is stored; the raw token is shown once at creation.
    """

    __tablename__ = "scim_tokens"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    hashed_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScimToken {self.token_prefix}… org={self.org_id}>"


class Invite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending email invitation to join an org. Token stored only as a hash."""

    __tablename__ = "invites"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_invite_org_email"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, native_enum=False, length=32),
        default=OrgRole.VIEWER,
        nullable=False,
    )
    token_prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    hashed_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, native_enum=False, length=16),
        default=InviteStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Invite {self.email} org={self.org_id} {self.status}>"
