"""Pure-logic tests for the permission engine.

Two things are exercised here and NOTHING touches a database:

* the permission-ordering helpers (:data:`PERMISSION_ORDER`, :func:`permission_at_least`,
  :func:`max_permission`) across every level, and
* :meth:`RetrievalScope.apply` - the SQL-pushdown predicate that guarantees a chunk the
  caller cannot view can never leave Postgres. It is asserted by *compiling* the
  statement to SQL text (never executing it) with ``literal_binds`` against the
  PostgreSQL dialect, so the exact org/collection/document UUIDs are visible in the
  emitted predicate.
"""

from __future__ import annotations

import asyncio
import types
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext
from app.models.access import AccessGrant
from app.models.chunk import DocumentChunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.enums import (
    PERMISSION_ORDER,
    OrgRole,
    PermissionLevel,
    PrincipalType,
    ResourceType,
    Visibility,
    max_permission,
    permission_at_least,
)
from app.models.team import Team, TeamMember
from app.services.permissions import RetrievalScope, build_retrieval_scope, user_team_ids

ALL_LEVELS = [
    PermissionLevel.NONE,
    PermissionLevel.VIEWER,
    PermissionLevel.EDITOR,
    PermissionLevel.MANAGER,
]


# --------------------------------------------------------------------------- #
# PERMISSION_ORDER + ordering helpers
# --------------------------------------------------------------------------- #
class TestPermissionOrder:
    def test_order_covers_every_level_and_is_strictly_increasing(self) -> None:
        assert set(PERMISSION_ORDER) == set(ALL_LEVELS)
        ranks = [PERMISSION_ORDER[level] for level in ALL_LEVELS]
        assert ranks == [0, 1, 2, 3]
        # Strictly increasing in declared order.
        assert all(a < b for a, b in zip(ranks, ranks[1:], strict=False))

    def test_none_is_the_floor(self) -> None:
        assert PERMISSION_ORDER[PermissionLevel.NONE] == 0
        assert min(PERMISSION_ORDER.values()) == 0

    def test_manager_is_the_ceiling(self) -> None:
        assert PERMISSION_ORDER[PermissionLevel.MANAGER] == max(PERMISSION_ORDER.values())


class TestPermissionAtLeast:
    def test_full_matrix_matches_numeric_order(self) -> None:
        for have in ALL_LEVELS:
            for need in ALL_LEVELS:
                expected = PERMISSION_ORDER[have] >= PERMISSION_ORDER[need]
                assert permission_at_least(have, need) is expected

    def test_reflexive(self) -> None:
        for level in ALL_LEVELS:
            assert permission_at_least(level, level) is True

    def test_strict_boundaries(self) -> None:
        assert permission_at_least(PermissionLevel.MANAGER, PermissionLevel.NONE) is True
        assert permission_at_least(PermissionLevel.VIEWER, PermissionLevel.EDITOR) is False
        assert permission_at_least(PermissionLevel.NONE, PermissionLevel.VIEWER) is False


class TestMaxPermission:
    def test_empty_is_none(self) -> None:
        assert max_permission() == PermissionLevel.NONE

    def test_picks_the_highest_regardless_of_argument_order(self) -> None:
        assert (
            max_permission(PermissionLevel.VIEWER, PermissionLevel.MANAGER, PermissionLevel.NONE)
            == PermissionLevel.MANAGER
        )
        assert (
            max_permission(PermissionLevel.MANAGER, PermissionLevel.NONE, PermissionLevel.VIEWER)
            == PermissionLevel.MANAGER
        )

    def test_idempotent_on_a_single_level(self) -> None:
        for level in ALL_LEVELS:
            assert max_permission(level) == level
            assert max_permission(level, level) == level

    def test_all_none_stays_none(self) -> None:
        assert max_permission(PermissionLevel.NONE, PermissionLevel.NONE) == PermissionLevel.NONE

    def test_commutative_over_every_pair(self) -> None:
        for a in ALL_LEVELS:
            for b in ALL_LEVELS:
                assert max_permission(a, b) == max_permission(b, a)


# --------------------------------------------------------------------------- #
# RetrievalScope.apply - compiled SQL predicate (never executed)
# --------------------------------------------------------------------------- #
def _sql(scope: RetrievalScope) -> str:
    """Compile ``scope.apply(select(DocumentChunk.id))`` to PostgreSQL SQL text.

    ``literal_binds`` renders bound parameters inline so the exact UUIDs appear in the
    predicate; the PostgreSQL dialect renders UUIDs in canonical dashed form, matching
    ``str(uuid)``.
    """
    stmt = scope.apply(select(DocumentChunk.id))
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestRetrievalScopeIsEmpty:
    def test_default_scope_is_empty(self) -> None:
        assert RetrievalScope(org_id=uuid.uuid4()).is_empty is True

    def test_any_grant_makes_it_non_empty(self) -> None:
        oid = uuid.uuid4()
        assert RetrievalScope(org_id=oid, all_access=True).is_empty is False
        assert RetrievalScope(org_id=oid, collection_ids={uuid.uuid4()}).is_empty is False
        assert RetrievalScope(org_id=oid, extra_document_ids={uuid.uuid4()}).is_empty is False

    def test_denied_only_is_still_empty(self) -> None:
        # Denials without any allow set grant nothing.
        scope = RetrievalScope(org_id=uuid.uuid4(), denied_document_ids={uuid.uuid4()})
        assert scope.is_empty is True


class TestRetrievalScopeApply:
    def test_org_predicate_is_always_present(self) -> None:
        oid = uuid.uuid4()
        for scope in (
            RetrievalScope(org_id=oid, all_access=True),
            RetrievalScope(org_id=oid, collection_ids={uuid.uuid4()}),
            RetrievalScope(org_id=oid, extra_document_ids={uuid.uuid4()}),
            RetrievalScope(org_id=oid),
        ):
            sql = _sql(scope)
            assert "document_chunks.org_id = " in sql
            assert str(oid) in sql

    def test_all_access_has_no_id_filters(self) -> None:
        sql = _sql(RetrievalScope(org_id=uuid.uuid4(), all_access=True))
        assert " IN " not in sql
        assert "NOT IN" not in sql
        assert "IS NULL" not in sql

    def test_all_access_with_denials_only_excludes(self) -> None:
        did = uuid.uuid4()
        sql = _sql(RetrievalScope(org_id=uuid.uuid4(), all_access=True, denied_document_ids={did}))
        assert "document_chunks.document_id NOT IN" in sql
        assert str(did) in sql
        # No positive membership filter when all_access short-circuits.
        assert "collection_id IN" not in sql

    def test_all_access_ignores_collection_and_extra(self) -> None:
        # all_access takes precedence: any collection/extra sets must be bypassed so an
        # admin is never accidentally narrowed.
        cid = uuid.uuid4()
        did = uuid.uuid4()
        sql = _sql(
            RetrievalScope(
                org_id=uuid.uuid4(),
                all_access=True,
                collection_ids={cid},
                extra_document_ids={did},
            )
        )
        assert str(cid) not in sql
        assert str(did) not in sql
        assert " IN " not in sql

    def test_collection_ids_filter(self) -> None:
        cid = uuid.uuid4()
        sql = _sql(RetrievalScope(org_id=uuid.uuid4(), collection_ids={cid}))
        assert "document_chunks.collection_id IN" in sql
        assert str(cid) in sql
        assert "document_id IN" not in sql

    def test_extra_document_ids_filter(self) -> None:
        did = uuid.uuid4()
        sql = _sql(RetrievalScope(org_id=uuid.uuid4(), extra_document_ids={did}))
        assert "document_chunks.document_id IN" in sql
        assert str(did) in sql
        assert "collection_id IN" not in sql

    def test_collections_and_extra_documents_are_ored(self) -> None:
        cid = uuid.uuid4()
        did = uuid.uuid4()
        sql = _sql(
            RetrievalScope(
                org_id=uuid.uuid4(),
                collection_ids={cid},
                extra_document_ids={did},
            )
        )
        assert "document_chunks.collection_id IN" in sql
        assert "document_chunks.document_id IN" in sql
        assert " OR " in sql
        assert str(cid) in sql
        assert str(did) in sql

    def test_denied_documents_are_anded_as_exclusion(self) -> None:
        cid = uuid.uuid4()
        did = uuid.uuid4()
        sql = _sql(
            RetrievalScope(
                org_id=uuid.uuid4(),
                collection_ids={cid},
                denied_document_ids={did},
            )
        )
        assert "document_chunks.collection_id IN" in sql
        assert "document_chunks.document_id NOT IN" in sql
        assert str(cid) in sql
        assert str(did) in sql

    def test_no_access_compiles_to_impossible_predicate(self) -> None:
        # Not admin, no collections, no extra docs -> the caller can see nothing, which
        # MUST compile to a predicate that matches no rows.
        sql = _sql(RetrievalScope(org_id=uuid.uuid4()))
        assert "document_chunks.id IS NULL" in sql
        # And still scoped to the org.
        assert "document_chunks.org_id = " in sql

    def test_apply_returns_a_new_statement_and_keeps_base_columns(self) -> None:
        scope = RetrievalScope(org_id=uuid.uuid4(), all_access=True)
        base = select(DocumentChunk.id)
        applied = scope.apply(base)
        # A WHERE clause was added.
        assert applied.whereclause is not None
        assert base.whereclause is None  # original left untouched
        assert "SELECT document_chunks.id" in str(applied)


@pytest.mark.parametrize("bad", ["", "nope"])
def test_permission_order_lookup_only_accepts_known_levels(bad: str) -> None:
    # Guards against silent widening if a raw string sneaks into the ordering helpers.
    with pytest.raises((KeyError, TypeError)):
        permission_at_least(bad, PermissionLevel.NONE)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Nested-team expansion - user_team_ids and the retrieval scope it feeds.
#
# These exercise the REAL async functions against an in-memory stub of the sliver of the
# ``AsyncSession`` API they touch. Nothing hits a database: the stub answers each query by
# the mapped entity it selects (unambiguous - every query in the flow targets a distinct
# entity), returning exactly the rows the real query would. The point under test is the
# single engine change: a user's effective team set is their DIRECT teams PLUS every
# ANCESTOR (grants flow strictly down), and both the route path and retrieval inherit it.
# --------------------------------------------------------------------------- #
class _Result:
    """The narrow slice of a SQLAlchemy ``Result`` the permission engine consumes."""

    def __init__(self, *, scalar_values: list | None = None, rows: list | None = None) -> None:
        self._scalar_values = scalar_values or []
        self._rows = rows or []

    def scalars(self) -> _Result:
        return self

    def all(self) -> list:
        # ``.scalars().all()`` yields scalar values; a bare ``.all()`` yields row tuples.
        return list(self._scalar_values) if self._scalar_values else list(self._rows)


class _StubSession:
    """In-memory stand-in for ``AsyncSession.execute``.

    Dispatches on the mapped entity of the statement so it stays honest about which query
    it is answering, and returns rows exactly as the real query would (e.g. only grants
    whose principal is in the team set, since retrieval filters those in SQL).
    """

    def __init__(
        self,
        *,
        direct_team_ids: list[uuid.UUID],
        parent_of: dict[uuid.UUID, uuid.UUID | None],
        collections: list[Collection] | None = None,
        grants: list[AccessGrant] | None = None,
        doc_overrides: list[tuple] | None = None,
    ) -> None:
        self._direct = direct_team_ids
        self._parent_of = parent_of
        self._collections = collections or []
        self._grants = grants or []
        self._doc_overrides = doc_overrides or []

    async def execute(self, stmt) -> _Result:  # noqa: ANN001
        entity = stmt.column_descriptions[0].get("entity")
        if entity is TeamMember:
            return _Result(scalar_values=list(self._direct))
        if entity is Team:
            return _Result(rows=list(self._parent_of.items()))
        if entity is Collection:
            return _Result(scalar_values=list(self._collections))
        if entity is AccessGrant:
            return _Result(scalar_values=list(self._grants))
        if entity is Document:
            return _Result(rows=list(self._doc_overrides))
        raise AssertionError(f"unexpected query entity: {entity!r}")


def _ctx(org_id: uuid.UUID, user_id: uuid.UUID) -> AuthContext:
    # A non-admin so build_retrieval_scope does not short-circuit to all_access; user is a
    # duck-typed stand-in exposing only the ``.id`` the ``user_id`` property reads.
    return AuthContext(
        org_id=org_id, org_role=OrgRole.VIEWER, user=types.SimpleNamespace(id=user_id)
    )


class TestUserTeamIdsAncestorExpansion:
    def test_direct_membership_expands_to_all_ancestors(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        root, mid, leaf = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        db = _StubSession(
            direct_team_ids=[leaf],
            parent_of={leaf: mid, mid: root, root: None},
        )
        result = asyncio.run(user_team_ids(db, _ctx(org, user)))
        # The whole chain leaf -> mid -> root is included.
        assert result == {leaf, mid, root}

    def test_upward_only_a_parent_member_never_inherits_a_child_team(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        parent, child = uuid.uuid4(), uuid.uuid4()
        db = _StubSession(
            direct_team_ids=[parent],
            parent_of={child: parent, parent: None},
        )
        result = asyncio.run(user_team_ids(db, _ctx(org, user)))
        # Grants flow DOWN: a parent-team member gains nothing from the sub-team.
        assert result == {parent}
        assert child not in result

    def test_no_membership_returns_empty_without_walking_the_tree(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        other = uuid.uuid4()
        db = _StubSession(direct_team_ids=[], parent_of={other: None})
        assert asyncio.run(user_team_ids(db, _ctx(org, user))) == set()

    def test_no_user_returns_empty(self) -> None:
        ctx = AuthContext(org_id=uuid.uuid4(), org_role=OrgRole.VIEWER, user=None)
        db = _StubSession(direct_team_ids=[uuid.uuid4()], parent_of={})
        assert asyncio.run(user_team_ids(db, ctx)) == set()

    def test_two_node_cycle_terminates(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        a, b = uuid.uuid4(), uuid.uuid4()
        db = _StubSession(direct_team_ids=[a], parent_of={a: b, b: a})
        # A malformed A <-> B cycle must terminate (the result set is the visited guard).
        result = asyncio.run(user_team_ids(db, _ctx(org, user)))
        assert result == {a, b}

    def test_self_referential_cycle_terminates(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        a = uuid.uuid4()
        db = _StubSession(direct_team_ids=[a], parent_of={a: a})
        assert asyncio.run(user_team_ids(db, _ctx(org, user))) == {a}


class _CapturingSession(_StubSession):
    """A stub session that also records every executed statement for SQL inspection."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.statements: list = []

    async def execute(self, stmt) -> _Result:  # noqa: ANN001
        self.statements.append(stmt)
        return await super().execute(stmt)


class TestUserTeamIdsIsOrgScoped:
    """The direct-membership query must be scoped to ``ctx.org_id`` so a user's team
    memberships in OTHER orgs can never leak into the effective set (the module-wide
    org-scoping invariant). The stub returns rows unfiltered, so this pins the SQL itself.
    """

    def test_direct_membership_query_joins_teams_and_filters_by_the_callers_org(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        team = uuid.uuid4()
        db = _CapturingSession(direct_team_ids=[team], parent_of={team: None})
        asyncio.run(user_team_ids(db, _ctx(org, user)))

        membership_stmt = next(
            s for s in db.statements if s.column_descriptions[0].get("entity") is TeamMember
        )
        sql = str(
            membership_stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "JOIN teams" in sql
        assert f"teams.org_id = '{org}'" in sql
        assert f"team_members.user_id = '{user}'" in sql


class TestRetrievalScopeInheritsAncestorTeams:
    def test_sub_team_member_scope_includes_ancestor_collections_and_grants(self) -> None:
        org, user = uuid.uuid4(), uuid.uuid4()
        ancestor, leaf, unrelated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        # A TEAM-visibility collection owned by the ANCESTOR team is reachable because the
        # sub-team member inherits the ancestor; one owned by an UNRELATED team is not.
        c_team = Collection(
            id=uuid.uuid4(),
            org_id=org,
            owner_id=None,
            owner_team_id=ancestor,
            visibility=Visibility.TEAM,
            default_permission=PermissionLevel.VIEWER,
        )
        c_hidden = Collection(
            id=uuid.uuid4(),
            org_id=org,
            owner_id=None,
            owner_team_id=unrelated,
            visibility=Visibility.TEAM,
            default_permission=PermissionLevel.VIEWER,
        )
        # A PRIVATE collection reachable only via an explicit grant to the ANCESTOR team.
        c_grant = Collection(
            id=uuid.uuid4(),
            org_id=org,
            owner_id=None,
            owner_team_id=None,
            visibility=Visibility.PRIVATE,
            default_permission=PermissionLevel.VIEWER,
        )
        grant = AccessGrant(
            org_id=org,
            resource_type=ResourceType.COLLECTION,
            resource_id=c_grant.id,
            principal_type=PrincipalType.TEAM,
            principal_id=ancestor,
            permission=PermissionLevel.VIEWER,
        )

        db = _StubSession(
            direct_team_ids=[leaf],
            parent_of={leaf: ancestor, ancestor: None},
            collections=[c_team, c_hidden, c_grant],
            grants=[grant],
        )
        scope = asyncio.run(build_retrieval_scope(db, _ctx(org, user)))

        # Ancestor team confers BOTH the TEAM-visibility collection and the team grant.
        assert c_team.id in scope.collection_ids
        assert c_grant.id in scope.collection_ids
        # A TEAM collection owned by an unrelated team stays out (upward-only, not sideways).
        assert c_hidden.id not in scope.collection_ids

        sql = _sql(scope)
        assert str(c_team.id) in sql
        assert str(c_grant.id) in sql
        assert str(c_hidden.id) not in sql
