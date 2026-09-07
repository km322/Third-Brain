"""Factories that build **real** rows in the test Postgres for integration tests.

Every factory persists through the passed-in :class:`AsyncSession` and **commits**, so
the created state is visible to the independent sessions the ASGI app opens per request
(``get_db``). Nothing here is a mock: documents are chunked with the real chunker and
embedded through :func:`app.services.llm.embed_texts` (the deterministic offline ``fake``
provider), so genuine ``pgvector`` rows exist and cosine search returns true hits.

The helpers are deliberately small and composable; tests reach for them to arrange
state directly (fast) and drive the HTTP surface for the behaviour under test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_api_key, hash_password
from app.models.access import AccessGrant
from app.models.api_key import ApiKey
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.connector import Connector
from app.models.document import Document
from app.models.enums import (
    ConnectorPurpose,
    ConnectorType,
    DocumentStatus,
    MembershipStatus,
    OrgRole,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    SourceType,
    Visibility,
)
from app.models.organization import Organization
from app.models.team import Team, TeamMember
from app.models.user import Membership, User
from app.services.chunking import chunk_text
from app.services.llm import embed_texts


def _rand(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _slug(name: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "x"
    # Suffix keeps org-scoped unique constraints (slug) collision-free across a run.
    return f"{base}-{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------- #
# Organizations, users, memberships
# --------------------------------------------------------------------------- #
async def create_org(db: AsyncSession, *, name: str | None = None) -> Organization:
    name = name or f"Org {_rand()}"
    org = Organization(name=name, slug=_slug(name))
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


async def create_user(
    db: AsyncSession,
    *,
    email: str | None = None,
    password: str = "Sup3rSecret!",
    full_name: str = "Test User",
    is_active: bool = True,
) -> User:
    user = User(
        email=(email or f"{_rand('user-')}@example.com").lower(),
        hashed_password=hash_password(password),
        full_name=full_name,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_membership(
    db: AsyncSession,
    *,
    org: Organization,
    user: User,
    role: OrgRole = OrgRole.VIEWER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> Membership:
    membership = Membership(org_id=org.id, user_id=user.id, role=role, status=status)
    db.add(membership)
    await db.commit()
    await db.refresh(membership)
    return membership


async def create_org_with_owner(
    db: AsyncSession,
    *,
    org_name: str | None = None,
    email: str | None = None,
    password: str = "Sup3rSecret!",
    full_name: str = "Owner",
) -> tuple[Organization, User, Membership]:
    """Create an org plus an ``OWNER`` member - the common bootstrap for a tenant."""
    org = await create_org(db, name=org_name)
    user = await create_user(db, email=email, password=password, full_name=full_name)
    membership = await create_membership(db, org=org, user=user, role=OrgRole.OWNER)
    return org, user, membership


async def add_member(
    db: AsyncSession,
    *,
    org: Organization,
    role: OrgRole = OrgRole.VIEWER,
    email: str | None = None,
    full_name: str = "Member",
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> tuple[User, Membership]:
    """Create a fresh user and attach them to ``org`` with ``role`` (active by default)."""
    user = await create_user(db, email=email, full_name=full_name)
    membership = await create_membership(db, org=org, user=user, role=role, status=status)
    return user, membership


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #
async def create_team(db: AsyncSession, *, org: Organization, name: str | None = None) -> Team:
    name = name or f"Team {_rand()}"
    team = Team(org_id=org.id, name=name, slug=_slug(name))
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


async def add_user_to_team(db: AsyncSession, *, team: Team, user: User) -> TeamMember:
    link = TeamMember(team_id=team.id, user_id=user.id)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
async def create_api_key(
    db: AsyncSession,
    *,
    org: Organization,
    name: str = "test-key",
    scopes: list[str] | tuple[str, ...] = ("search",),
    rate_limit_per_minute: int = 120,
    acts_as_user: User | None = None,
    created_by: User | None = None,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> tuple[ApiKey, str]:
    """Mint an API key and return ``(row, raw_secret)``.

    Only the SHA-256 hash + prefix are stored, exactly like the real endpoint, so the
    returned raw secret is the sole way to authenticate with it afterwards.
    """
    full_key, prefix, hashed = generate_api_key()
    key = ApiKey(
        org_id=org.id,
        created_by_id=created_by.id if created_by else None,
        acts_as_user_id=acts_as_user.id if acts_as_user else None,
        name=name,
        key_prefix=prefix,
        hashed_key=hashed,
        scopes=list(scopes),
        rate_limit_per_minute=rate_limit_per_minute,
        expires_at=expires_at,
        revoked=revoked,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key, full_key


def api_key_headers(raw_secret: str) -> dict[str, str]:
    """Authorization header for a raw API-key secret (``tb_...``)."""
    return {"Authorization": f"Bearer {raw_secret}"}


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
async def create_collection(
    db: AsyncSession,
    *,
    org: Organization,
    owner: User | None = None,
    owner_team: Team | None = None,
    name: str | None = None,
    visibility: Visibility = Visibility.PRIVATE,
    default_permission: PermissionLevel = PermissionLevel.VIEWER,
    description: str | None = None,
) -> Collection:
    name = name or f"Collection {_rand()}"
    collection = Collection(
        org_id=org.id,
        owner_id=owner.id if owner else None,
        owner_team_id=owner_team.id if owner_team else None,
        name=name,
        slug=_slug(name),
        description=description,
        visibility=visibility,
        default_permission=default_permission,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dim=settings.EMBEDDING_DIM,
        document_count=0,
    )
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return collection


# --------------------------------------------------------------------------- #
# Documents (+ embedded chunks - real pgvector rows)
# --------------------------------------------------------------------------- #
async def create_document(
    db: AsyncSession,
    *,
    org: Organization,
    collection: Collection,
    title: str = "Test Document",
    content: str = "The quick brown fox jumps over the lazy dog.",
    created_by: User | None = None,
    visibility: Visibility | None = None,
    indexed: bool = True,
) -> Document:
    """Create a document and, when ``indexed``, its embedded chunks.

    Chunks are produced by the real chunker and embedded via the LLM facade, so the
    resulting rows carry genuine ``pgvector`` embeddings that cosine search can rank.
    """
    document = Document(
        org_id=org.id,
        collection_id=collection.id,
        created_by_id=created_by.id if created_by else None,
        title=title[:1024],
        source_type=SourceType.TEXT,
        mime_type="text/plain",
        visibility=visibility,
        status=DocumentStatus.PENDING,
        size_bytes=len(content.encode("utf-8")),
    )
    db.add(document)
    collection.document_count = (collection.document_count or 0) + 1
    await db.flush()

    if indexed:
        chunks = chunk_text(content, model=collection.embedding_model)
        if chunks:
            embedded = await embed_texts(
                [c.content for c in chunks], model=collection.embedding_model
            )
            for chunk, vector in zip(chunks, embedded.vectors, strict=False):
                db.add(
                    DocumentChunk(
                        org_id=org.id,
                        collection_id=collection.id,
                        document_id=document.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding=vector,
                        meta={},
                    )
                )
        document.chunk_count = len(chunks)
        document.status = DocumentStatus.INDEXED
        document.indexed_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(document)
    return document


# --------------------------------------------------------------------------- #
# Access grants (ACLs)
# --------------------------------------------------------------------------- #
async def create_access_grant(
    db: AsyncSession,
    *,
    org: Organization,
    resource_type: ResourceType,
    resource_id: uuid.UUID,
    principal_type: PrincipalType,
    principal_id: uuid.UUID,
    permission: PermissionLevel = PermissionLevel.VIEWER,
    granted_by: User | None = None,
) -> AccessGrant:
    grant = AccessGrant(
        org_id=org.id,
        resource_type=resource_type,
        resource_id=resource_id,
        principal_type=principal_type,
        principal_id=principal_id,
        permission=permission,
        granted_by_id=granted_by.id if granted_by else None,
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return grant


async def grant_user(
    db: AsyncSession,
    *,
    org: Organization,
    user: User,
    resource_type: ResourceType,
    resource_id: uuid.UUID,
    permission: PermissionLevel = PermissionLevel.VIEWER,
) -> AccessGrant:
    """Convenience: grant a single user ``permission`` on a resource."""
    return await create_access_grant(
        db,
        org=org,
        resource_type=resource_type,
        resource_id=resource_id,
        principal_type=PrincipalType.USER,
        principal_id=user.id,
        permission=permission,
    )


# --------------------------------------------------------------------------- #
# Connectors
# --------------------------------------------------------------------------- #
async def create_connector(
    db: AsyncSession,
    *,
    org: Organization,
    name: str = "Test Connector",
    type: ConnectorType = ConnectorType.OPENAI,
    purpose: ConnectorPurpose = ConnectorPurpose.EMBEDDING,
    model: str | None = None,
    credentials: dict | None = None,
    is_default: bool = True,
    enabled: bool = True,
) -> Connector:
    import json

    from app.core.security import encrypt_secret

    connector = Connector(
        org_id=org.id,
        name=name,
        type=type,
        purpose=purpose,
        model=model or settings.EMBEDDING_MODEL,
        config={},
        encrypted_credentials=(encrypt_secret(json.dumps(credentials)) if credentials else None),
        is_default=is_default,
        enabled=enabled,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return connector
