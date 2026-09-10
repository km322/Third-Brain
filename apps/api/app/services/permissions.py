"""The permission engine - the single source of truth for "who can do what".

Two callers rely on this module and must always agree:

1. **Route authorization** - :func:`effective_permission` / :func:`require_permission`.
2. **Retrieval filtering** - :func:`build_retrieval_scope` produces a SQL predicate so a
   chunk the caller cannot view can never enter a search result (and therefore never a
   prompt).

Effective permission on a resource is the MAXIMUM of:
    * org role baseline (owners/admins => MANAGER everywhere),
    * ownership (collection owner => MANAGER),
    * visibility baseline (ORG/PUBLIC/TEAM => collection.default_permission),
    * direct user grants, and best team grants,
    * for documents: everything inherited from the parent collection.

:func:`document_audience` answers the inverse question - "who can see this?" - in the same
set form, for the quarantine review screen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.access import AccessGrant
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    PERMISSION_ORDER,
    MembershipStatus,
    OrgRole,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    Visibility,
    max_permission,
    permission_at_least,
)
from app.models.team import Team, TeamMember
from app.models.user import Membership, User


async def user_team_ids(db: AsyncSession, ctx: AuthContext) -> set[uuid.UUID]:
    """Return the caller's effective team ids: their DIRECT team memberships PLUS every
    ANCESTOR team (parent, grandparent, ...).

    Team grants flow strictly DOWNWARD, so expansion is upward-only: a member of a nested
    team inherits the access of every team above it, but a parent-team member never gains a
    sub-team-only grant (descendants are never added). This is the single expansion point
    that both enforcement sites -- :func:`effective_permission` and
    :func:`build_retrieval_scope` -- share, so TEAM-visibility owners and team access grants
    pick up ancestors identically and route authorization can never disagree with retrieval.

    The upward walk accumulates into a single result set that doubles as the visited guard,
    so a malformed cycle in the team tree terminates.
    """
    if ctx.user_id is None:
        return set()
    rows = await db.execute(
        select(TeamMember.team_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == ctx.user_id, Team.org_id == ctx.org_id)
    )
    direct = set(rows.scalars().all())
    if not direct:
        return direct

    parent_rows = await db.execute(
        select(Team.id, Team.parent_team_id).where(Team.org_id == ctx.org_id)
    )
    parent_of = dict(parent_rows.all())

    result: set[uuid.UUID] = set()
    for team_id in direct:
        current: uuid.UUID | None = team_id
        while current is not None and current not in result:
            result.add(current)
            current = parent_of.get(current)
    return result


def _visibility_grants_access(
    visibility: Visibility,
    ctx: AuthContext,
    owner_team_id: uuid.UUID | None,
    team_ids: set[uuid.UUID],
) -> bool:
    """Whether ``visibility`` alone puts the resource in reach of the caller.

    PRIVATE is the fall-through: it never grants access on its own, only ownership, an
    explicit grant or the org-admin baseline can reach a private resource.
    """
    if visibility in (Visibility.ORG, Visibility.PUBLIC):
        return True
    if visibility == Visibility.TEAM:
        return owner_team_id is not None and owner_team_id in team_ids
    return False


async def _grants_for(
    db: AsyncSession,
    ctx: AuthContext,
    team_ids: set[uuid.UUID],
    targets: list[tuple[ResourceType, uuid.UUID]],
) -> PermissionLevel:
    """Best explicit grant (user or team) across the given (type, id) targets."""
    if not targets:
        return PermissionLevel.NONE
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
        return PermissionLevel.NONE

    resource_clauses = [
        and_(AccessGrant.resource_type == rtype, AccessGrant.resource_id == rid)
        for rtype, rid in targets
    ]
    rows = await db.execute(
        select(AccessGrant.permission).where(
            AccessGrant.org_id == ctx.org_id,
            or_(*resource_clauses),
            or_(*principal_clauses),
        )
    )
    return max_permission(PermissionLevel.NONE, *rows.scalars().all())


async def effective_permission(
    db: AsyncSession,
    ctx: AuthContext,
    resource_type: ResourceType,
    resource_id: uuid.UUID,
) -> PermissionLevel:
    """Compute the caller's effective permission on a collection or document.

    Org owners/admins short-circuit to MANAGER: they have full control over everything in
    their org. For a document, each access source is computed independently and the MAXIMUM
    taken. The document's own visibility (when set) OVERRIDES the collection's for the
    *visibility baseline* only - explicit grants (on the collection, inherited, or on the
    document itself), ownership and admin always still apply - and access via the visibility
    baseline confers the collection's ``default_permission``.
    """
    if ctx.is_admin:
        return PermissionLevel.MANAGER

    team_ids = await user_team_ids(db, ctx)

    if resource_type == ResourceType.COLLECTION:
        collection = await db.get(Collection, resource_id)
        if collection is None or collection.org_id != ctx.org_id:
            return PermissionLevel.NONE
        return await _collection_permission(db, ctx, collection, team_ids)

    document = await db.get(Document, resource_id)
    if document is None or document.org_id != ctx.org_id:
        return PermissionLevel.NONE
    collection = await db.get(Collection, document.collection_id)
    if collection is None:
        return PermissionLevel.NONE

    levels: list[PermissionLevel] = [PermissionLevel.NONE]
    if ctx.user_id is not None and collection.owner_id == ctx.user_id:
        levels.append(PermissionLevel.MANAGER)

    levels.append(
        await _grants_for(
            db,
            ctx,
            team_ids,
            [
                (ResourceType.COLLECTION, collection.id),
                (ResourceType.DOCUMENT, document.id),
            ],
        )
    )

    effective_visibility = document.visibility or collection.visibility
    if _visibility_grants_access(effective_visibility, ctx, collection.owner_team_id, team_ids):
        levels.append(collection.default_permission)

    return max_permission(*levels)


async def _collection_permission(
    db: AsyncSession,
    ctx: AuthContext,
    collection: Collection,
    team_ids: set[uuid.UUID],
) -> PermissionLevel:
    levels: list[PermissionLevel] = [PermissionLevel.NONE]
    if ctx.user_id is not None and collection.owner_id == ctx.user_id:
        levels.append(PermissionLevel.MANAGER)
    if _visibility_grants_access(collection.visibility, ctx, collection.owner_team_id, team_ids):
        levels.append(collection.default_permission)
    grant = await _grants_for(db, ctx, team_ids, [(ResourceType.COLLECTION, collection.id)])
    levels.append(grant)
    return max_permission(*levels)


async def require_permission(
    db: AsyncSession,
    ctx: AuthContext,
    resource_type: ResourceType,
    resource_id: uuid.UUID,
    needed: PermissionLevel,
) -> PermissionLevel:
    have = await effective_permission(db, ctx, resource_type, resource_id)
    if not permission_at_least(have, needed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires '{needed.value}' permission on this {resource_type.value}",
        )
    return have


@dataclass
class RetrievalScope:
    """A resolved, org-scoped view of what chunks the caller may retrieve."""

    org_id: uuid.UUID
    all_access: bool = False
    collection_ids: set[uuid.UUID] = field(default_factory=set)
    extra_document_ids: set[uuid.UUID] = field(default_factory=set)
    denied_document_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        return not self.all_access and not self.collection_ids and not self.extra_document_ids

    def apply(self, stmt: Select) -> Select:
        """Add the visibility predicate to a select over DocumentChunk.

        A caller with no access at all yields an impossible predicate rather than an
        unconstrained statement.
        """
        stmt = stmt.where(DocumentChunk.org_id == self.org_id)
        if self.all_access:
            if self.denied_document_ids:
                stmt = stmt.where(DocumentChunk.document_id.notin_(self.denied_document_ids))
            return stmt
        allow = []
        if self.collection_ids:
            allow.append(DocumentChunk.collection_id.in_(self.collection_ids))
        if self.extra_document_ids:
            allow.append(DocumentChunk.document_id.in_(self.extra_document_ids))
        if not allow:
            return stmt.where(DocumentChunk.id.is_(None))
        stmt = stmt.where(or_(*allow))
        if self.denied_document_ids:
            stmt = stmt.where(DocumentChunk.document_id.notin_(self.denied_document_ids))
        return stmt


async def build_retrieval_scope(
    db: AsyncSession,
    ctx: AuthContext,
    collection_ids: list[uuid.UUID] | None = None,
) -> RetrievalScope:
    """Resolve the caller's visible chunk universe, optionally narrowed to
    ``collection_ids`` explicitly requested by the search call.

    Retrieval must resolve visibility EXACTLY as :func:`effective_permission` does, or the
    two enforcement sites drift and a chunk the route 403s can leak into a result (or a
    chunk the caller is entitled to silently vanishes). We mirror that logic here in set
    form: ownership and grants confer access to a whole collection regardless of a
    document's own visibility, while collection *visibility* only confers access when the
    collection's ``default_permission`` is >= VIEWER and is overridable by a document's own
    (more or less restrictive) visibility. Only five collection attributes are read, so the
    query selects just those columns instead of hydrating full ORM ``Collection`` instances
    for every collection in the org.

    Resolution proceeds in three steps:

    1. Collection-level access, split by strength. ``strong_collections`` - owner or
       explicit collection grant: every document is visible, even one with a
       more-restrictive doc-level visibility. ``visibility_collections`` - reachable via the
       collection's own visibility (and ``default_permission`` >= VIEWER); a document's own
       visibility can still restrict it.
    2. Explicit grants (>= VIEWER). A collection grant strengthens a whole collection; a
       document grant confers access to a single document.
    3. Reconcile documents whose OWN visibility differs from their collection's, so that
       retrieval matches :func:`effective_permission` for both restrictive overrides (a
       stricter doc-level visibility revokes a document inside an otherwise visible
       collection) and permissive/upward overrides (a more-permissive doc visibility, e.g.
       an ORG document inside a PRIVATE collection, raises just that document). Documents
       reached by ownership, a collection grant, or a direct document grant are always
       visible and skip this step.

    When the search narrows to ``collection_ids``, directly-granted and upward-override
    documents that live in other collections must not surface, so they are filtered to the
    requested collections too.
    """
    requested = set(collection_ids) if collection_ids else None

    if ctx.is_admin:
        scope = RetrievalScope(org_id=ctx.org_id, all_access=True)
        if requested is not None:
            scope.all_access = False
            scope.collection_ids = requested
        return scope

    team_ids = await user_team_ids(db, ctx)

    collections = (
        await db.execute(
            select(
                Collection.id,
                Collection.owner_id,
                Collection.owner_team_id,
                Collection.visibility,
                Collection.default_permission,
            ).where(Collection.org_id == ctx.org_id)
        )
    ).all()
    by_id = {c.id: c for c in collections}

    strong_collections: set[uuid.UUID] = set()
    visibility_collections: set[uuid.UUID] = set()
    for c in collections:
        if ctx.user_id is not None and c.owner_id == ctx.user_id:
            strong_collections.add(c.id)
        elif _visibility_grants_access(
            c.visibility, ctx, c.owner_team_id, team_ids
        ) and permission_at_least(c.default_permission, PermissionLevel.VIEWER):
            visibility_collections.add(c.id)

    principal_filter = []
    if ctx.user_id is not None:
        principal_filter.append(
            and_(
                AccessGrant.principal_type == PrincipalType.USER,
                AccessGrant.principal_id == ctx.user_id,
            )
        )
    if team_ids:
        principal_filter.append(
            and_(
                AccessGrant.principal_type == PrincipalType.TEAM,
                AccessGrant.principal_id.in_(team_ids),
            )
        )
    extra_documents: set[uuid.UUID] = set()
    if principal_filter:
        grants = (
            (
                await db.execute(
                    select(AccessGrant).where(
                        AccessGrant.org_id == ctx.org_id, or_(*principal_filter)
                    )
                )
            )
            .scalars()
            .all()
        )
        for g in grants:
            if not permission_at_least(g.permission, PermissionLevel.VIEWER):
                continue
            if g.resource_type == ResourceType.COLLECTION:
                strong_collections.add(g.resource_id)
            else:
                extra_documents.add(g.resource_id)

    visible_collections = strong_collections | visibility_collections

    denied: set[uuid.UUID] = set()
    override_rows = (
        await db.execute(
            select(Document.id, Document.collection_id, Document.visibility).where(
                Document.org_id == ctx.org_id,
                Document.visibility.is_not(None),
            )
        )
    ).all()
    for doc_id, coll_id, vis in override_rows:
        collection = by_id.get(coll_id)
        if collection is None or coll_id in strong_collections or doc_id in extra_documents:
            continue
        doc_grants_access = _visibility_grants_access(
            vis, ctx, collection.owner_team_id, team_ids
        ) and permission_at_least(collection.default_permission, PermissionLevel.VIEWER)
        if coll_id in visibility_collections:
            if not doc_grants_access:
                denied.add(doc_id)
        elif doc_grants_access:
            extra_documents.add(doc_id)

    if requested is not None:
        visible_collections &= requested
        if extra_documents:
            kept = (
                (
                    await db.execute(
                        select(Document.id).where(
                            Document.org_id == ctx.org_id,
                            Document.id.in_(extra_documents),
                            Document.collection_id.in_(requested),
                        )
                    )
                )
                .scalars()
                .all()
            )
            extra_documents = set(kept)

    return RetrievalScope(
        org_id=ctx.org_id,
        all_access=False,
        collection_ids=visible_collections,
        extra_document_ids=extra_documents,
        denied_document_ids=denied,
    )


@dataclass(frozen=True)
class AudienceEntry:
    """One user's resolved access to a collection, with the source that grants it."""

    user_id: uuid.UUID
    name: str | None
    email: str
    permission: PermissionLevel
    via: str


def _team_descendant_ids(
    root_id: uuid.UUID, children_of: dict[uuid.UUID | None, list[uuid.UUID]]
) -> set[uuid.UUID]:
    """Return ``root_id`` plus every DESCENDANT team id.

    The inverse of :func:`user_team_ids`'s upward walk: because a member of a nested team
    inherits its ancestors' access, TEAM visibility or a team grant on a parent team
    reaches the members of all of its sub-teams. The result set doubles as the visited
    guard so a malformed cycle in the tree terminates.
    """
    result: set[uuid.UUID] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children_of.get(current, []))
    return result


async def document_audience(
    db: AsyncSession, collection: Collection, document: Document, *, limit: int = 50
) -> tuple[list[AudienceEntry], int, str | None, dict[str, int]]:
    """Resolve which org members could read ``document`` once indexed, and how.

    Mirrors :func:`effective_permission`'s access sources in set form so the quarantine
    review screen can answer "who will be able to read this document once indexed?". It is
    document-aware, exactly as route authorization is: the visibility baseline uses the
    document's own ``visibility`` when set, else the collection's (an ORG/PUBLIC override
    opens the document to every member even inside a PRIVATE collection; a stricter
    override such as PRIVATE on an ORG collection drops the visibility-derived entries),
    and explicit grants include both collection grants (inherited) and document-level
    grants. Org owners/admins and the collection owner are managers; TEAM visibility and
    team grants cover the target team and its descendant teams. Every team that can confer
    access (the TEAM-visibility owner team and each team grant's target) is expanded to its
    descendant set, then all of their members are resolved in one query.

    Each user keeps the MAXIMUM permission across sources, labelled with the source
    (``via``) that granted it. Source order matters: ``offer`` keeps the EARLIER
    source's label on ties, so user grants are offered before team grants and collection
    grants before document grants. A document-level visibility override is annotated in
    ``via`` so a reviewer can tell the reach comes from the document itself, not the
    collection it happens to sit in.

    Returns ``(entries, total_users, note, permission_counts)``: at most ``limit`` entries
    sorted by permission (strongest first) then name; the uncapped count of users with at
    least viewer access; for PUBLIC reach a note about unauthenticated API-key access; and
    a ``{"viewer": int, "editor": int, "manager": int}`` breakdown over the FULL audience.
    """
    effective_visibility = document.visibility or collection.visibility

    member_rows = (
        await db.execute(
            select(Membership.user_id, Membership.role, User.full_name, User.email)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == collection.org_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).all()
    members = {row.user_id: row for row in member_rows}

    team_rows = (
        await db.execute(
            select(Team.id, Team.parent_team_id, Team.name).where(Team.org_id == collection.org_id)
        )
    ).all()
    team_names = {row.id: row.name for row in team_rows}
    children_of: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for row in team_rows:
        children_of.setdefault(row.parent_team_id, []).append(row.id)

    grant_rows = (
        (
            await db.execute(
                select(AccessGrant).where(
                    AccessGrant.org_id == collection.org_id,
                    or_(
                        and_(
                            AccessGrant.resource_type == ResourceType.COLLECTION,
                            AccessGrant.resource_id == collection.id,
                        ),
                        and_(
                            AccessGrant.resource_type == ResourceType.DOCUMENT,
                            AccessGrant.resource_id == document.id,
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    collection_grants = [g for g in grant_rows if g.resource_type == ResourceType.COLLECTION]
    document_grants = [g for g in grant_rows if g.resource_type == ResourceType.DOCUMENT]

    team_scopes: dict[uuid.UUID, set[uuid.UUID]] = {}
    if effective_visibility == Visibility.TEAM and collection.owner_team_id is not None:
        team_scopes[collection.owner_team_id] = _team_descendant_ids(
            collection.owner_team_id, children_of
        )
    for grant in (*collection_grants, *document_grants):
        if grant.principal_type == PrincipalType.TEAM:
            team_scopes.setdefault(
                grant.principal_id, _team_descendant_ids(grant.principal_id, children_of)
            )
    members_of_scope: dict[uuid.UUID, set[uuid.UUID]] = {root: set() for root in team_scopes}
    scoped_teams: set[uuid.UUID] = set().union(*team_scopes.values()) if team_scopes else set()
    if scoped_teams:
        links = (
            await db.execute(
                select(TeamMember.team_id, TeamMember.user_id).where(
                    TeamMember.team_id.in_(scoped_teams)
                )
            )
        ).all()
        for team_id, user_id in links:
            for root, scope in team_scopes.items():
                if team_id in scope:
                    members_of_scope[root].add(user_id)

    best: dict[uuid.UUID, tuple[PermissionLevel, str]] = {}

    def offer(user_id: uuid.UUID, permission: PermissionLevel, via: str) -> None:
        """Keep the strongest permission per user; ties keep the earlier source's label."""
        if user_id not in members:
            return
        current = best.get(user_id)
        if current is None or PERMISSION_ORDER[permission] > PERMISSION_ORDER[current[0]]:
            best[user_id] = (permission, via)

    def offer_grants(grants: list[AccessGrant], principal_type: PrincipalType, via: str) -> None:
        """Offer one grant list, filtered to ``principal_type``. For USER grants ``via`` is
        the label; for TEAM grants it is the label PREFIX (the team name is appended) and
        the grant is fanned out to every member of the team's descendant scope."""
        for grant in grants:
            if grant.principal_type != principal_type:
                continue
            if principal_type == PrincipalType.USER:
                offer(grant.principal_id, grant.permission, via)
            else:
                label = f"{via}{team_names.get(grant.principal_id, str(grant.principal_id))}"
                for user_id in members_of_scope.get(grant.principal_id, set()):
                    offer(user_id, grant.permission, label)

    for user_id, row in members.items():
        if row.role in (OrgRole.OWNER, OrgRole.ADMIN):
            offer(user_id, PermissionLevel.MANAGER, "org-admin")
    if collection.owner_id is not None:
        offer(collection.owner_id, PermissionLevel.MANAGER, "collection-owner")
    offer_grants(collection_grants, PrincipalType.USER, "grant")
    offer_grants(document_grants, PrincipalType.USER, "document-grant")
    offer_grants(collection_grants, PrincipalType.TEAM, "team:")
    offer_grants(document_grants, PrincipalType.TEAM, "document-team:")

    doc_suffix = " (document)" if document.visibility is not None else ""
    if effective_visibility in (Visibility.ORG, Visibility.PUBLIC):
        base = "visibility:org" if effective_visibility == Visibility.ORG else "visibility:public"
        via = base + doc_suffix
        for user_id in members:
            offer(user_id, collection.default_permission, via)
    elif effective_visibility == Visibility.TEAM and collection.owner_team_id is not None:
        owner_name = team_names.get(collection.owner_team_id, str(collection.owner_team_id))
        via = f"visibility:team:{owner_name}{doc_suffix}"
        for user_id in members_of_scope.get(collection.owner_team_id, set()):
            offer(user_id, collection.default_permission, via)

    entries = [
        AudienceEntry(
            user_id=user_id,
            name=members[user_id].full_name,
            email=members[user_id].email,
            permission=permission,
            via=via,
        )
        for user_id, (permission, via) in best.items()
        if permission_at_least(permission, PermissionLevel.VIEWER)
    ]
    entries.sort(key=lambda e: (-PERMISSION_ORDER[e.permission], (e.name or e.email).lower()))
    permission_counts = {"viewer": 0, "editor": 0, "manager": 0}
    for entry in entries:
        permission_counts[entry.permission.value] += 1
    total_users = len(entries)
    note = (
        "Anyone with an API key for this organization can also read this document."
        if effective_visibility == Visibility.PUBLIC
        else None
    )
    return entries[:limit], total_users, note, permission_counts
