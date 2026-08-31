"""SQLAlchemy models.

Importing this package registers every model on ``Base.metadata`` so that Alembic
autogeneration and relationship resolution see the full schema. Import order avoids
circular-reference issues at class-definition time.
"""

from app.core.db import Base
from app.models.access import AccessGrant
from app.models.answer import Answer
from app.models.api_key import ApiKey
from app.models.audit import AuditLog
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.connector import Connector
from app.models.conversation import Conversation, ConversationMessage
from app.models.datasource import DataSource, DocumentExternalPrincipal, ExternalIdentity
from app.models.device_auth import DeviceAuthorization
from app.models.document import Document
from app.models.entity import DocumentEntity, Entity
from app.models.feedback import QueryInsight
from app.models.organization import Organization
from app.models.sso import FederatedIdentity, Invite, ScimToken, SsoConnection
from app.models.team import Team, TeamMember
from app.models.usage import UsageRecord
from app.models.user import Membership, User

__all__ = [
    "Base",
    "AccessGrant",
    "Answer",
    "ApiKey",
    "AuditLog",
    "Collection",
    "Connector",
    "Conversation",
    "ConversationMessage",
    "DataSource",
    "DeviceAuthorization",
    "Document",
    "DocumentChunk",
    "DocumentEntity",
    "DocumentExternalPrincipal",
    "Entity",
    "ExternalIdentity",
    "FederatedIdentity",
    "Invite",
    "Membership",
    "Organization",
    "QueryInsight",
    "ScimToken",
    "SsoConnection",
    "Team",
    "TeamMember",
    "UsageRecord",
    "User",
]
