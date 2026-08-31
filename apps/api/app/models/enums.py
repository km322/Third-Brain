"""Enumerations shared across the domain model.

All are ``str`` enums so they serialize cleanly to JSON and store as text.
"""

from __future__ import annotations

from enum import Enum


class OrgRole(str, Enum):
    """A user's role within an organization (membership)."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class TeamRole(str, Enum):
    """A user's role within a team (team membership)."""

    LEAD = "lead"
    MEMBER = "member"


class MembershipStatus(str, Enum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class Visibility(str, Enum):
    """Default reach of a collection/document, before explicit ACL grants."""

    PRIVATE = "private"  # only the owner + explicit grants + org admins
    TEAM = "team"  # members of the owning team
    ORG = "org"  # everyone in the organization
    PUBLIC = "public"  # anyone with an org API key (unauthenticated read)


class PermissionLevel(str, Enum):
    NONE = "none"
    VIEWER = "viewer"  # can read/search
    EDITOR = "editor"  # can add/update content
    MANAGER = "manager"  # can manage grants + delete


class PrincipalType(str, Enum):
    USER = "user"
    TEAM = "team"


class ResourceType(str, Enum):
    COLLECTION = "collection"
    DOCUMENT = "document"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    FILE = "file"
    TEXT = "text"
    URL = "url"
    CONNECTOR = "connector"


class ConnectorType(str, Enum):
    """LLM provider endpoints an org connector can point at.

    ``openai``/``azure_openai``/``ollama``/``custom`` all speak the OpenAI-compatible
    REST shape; ``anthropic`` and ``google`` use their native wire APIs (see
    :mod:`app.services.llm.providers`).
    """

    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"
    CUSTOM = "custom"  # any other OpenAI-compatible endpoint (base URL + key)
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class ConnectorPurpose(str, Enum):
    EMBEDDING = "embedding"
    COMPLETION = "completion"


class UsageKind(str, Enum):
    EMBEDDING = "embedding"
    COMPLETION = "completion"
    SEARCH = "search"
    INGEST = "ingest"


class AuditAction(str, Enum):
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_SSO_LOGIN = "user.sso_login"
    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"
    DEVICE_AUTH_APPROVED = "device_auth.approved"
    DEVICE_AUTH_DENIED = "device_auth.denied"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_REVOKED = "permission.revoked"
    DOCUMENT_CREATED = "document.created"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_ACCESSED = "document.accessed"
    DOCUMENT_QUARANTINED = "document.quarantined"
    DOCUMENT_QUARANTINE_APPROVED = "document.quarantine_approved"
    DOCUMENT_VERIFIED = "document.verified"
    DOCUMENT_UNVERIFIED = "document.unverified"
    DOCUMENT_SENSITIVE = "document.sensitive"
    COLLECTION_CREATED = "collection.created"
    SEARCH_PERFORMED = "search.performed"
    ANSWER_CREATED = "answer.created"
    ANSWER_DELETED = "answer.deleted"
    DATA_SOURCE_CREATED = "data_source.created"
    DATA_SOURCE_UPDATED = "data_source.updated"
    DATA_SOURCE_DELETED = "data_source.deleted"
    DATA_SOURCE_SYNCED = "data_source.synced"
    IDENTITY_MAPPED = "identity.mapped"
    MEMBER_INVITED = "member.invited"
    INVITE_ACCEPTED = "invite.accepted"
    INVITE_REVOKED = "invite.revoked"
    SSO_CONNECTION_UPDATED = "sso.connection_updated"
    SCIM_USER_PROVISIONED = "scim.user_provisioned"
    SCIM_USER_DEPROVISIONED = "scim.user_deprovisioned"
    SCIM_TOKEN_CREATED = "scim.token_created"


class DataSourceKind(str, Enum):
    """A knowledge data-source connector (distinct from the LLM-endpoint ``ConnectorType``)."""

    LOCAL_FOLDER = "local_folder"  # reference connector: a folder tree + sidecar ACLs
    GOOGLE_DRIVE = "google_drive"
    SLACK = "slack"
    GITHUB = "github"
    NOTION = "notion"
    CONFLUENCE = "confluence"


class DataSourceStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SYNCING = "syncing"
    ERROR = "error"


class ExternalPrincipalKind(str, Enum):
    """A principal in a source system's ACL: an individual or a group/role."""

    USER = "user"
    GROUP = "group"


class VerificationStatus(str, Enum):
    """Trust state of a document/answer (verified/fresh content)."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    STALE = "stale"  # was verified, but past its review-by date


class SensitivityLevel(str, Enum):
    """DLP classification applied by the sensitivity scan."""

    NONE = "none"
    PII = "pii"
    CONFIDENTIAL = "confidential"


class FeedbackRating(str, Enum):
    UP = "up"
    DOWN = "down"


class QueryKind(str, Enum):
    SEARCH = "search"
    CHAT = "chat"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class EntityKind(str, Enum):
    PERSON = "person"
    ORG = "org"
    PRODUCT = "product"
    PROJECT = "project"
    LOCATION = "location"
    OTHER = "other"


class SsoProtocol(str, Enum):
    OIDC = "oidc"
    SAML = "saml"


class InviteStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DeviceAuthStatus(str, Enum):
    """Lifecycle of a CLI device-authorization request (approve-in-browser flow)."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


# Ordering used by the permission engine to compute the "maximum" grant.
PERMISSION_ORDER: dict[PermissionLevel, int] = {
    PermissionLevel.NONE: 0,
    PermissionLevel.VIEWER: 1,
    PermissionLevel.EDITOR: 2,
    PermissionLevel.MANAGER: 3,
}


def permission_at_least(have: PermissionLevel, need: PermissionLevel) -> bool:
    return PERMISSION_ORDER[have] >= PERMISSION_ORDER[need]


def max_permission(*levels: PermissionLevel) -> PermissionLevel:
    best = PermissionLevel.NONE
    for level in levels:
        if PERMISSION_ORDER[level] > PERMISSION_ORDER[best]:
            best = level
    return best
