"""Unit tests for the permission math and the retrieval-scope SQL predicate.

These are pure (no database): the permission ordering helpers are exercised directly,
and :meth:`RetrievalScope.apply` is verified by compiling the resulting SQLAlchemy
``select`` to a string and asserting the expected ``WHERE`` predicates are present. This
is the security-critical guarantee that a chunk the caller cannot view can never leave
the database.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.chunk import DocumentChunk
from app.models.enums import PermissionLevel, max_permission, permission_at_least
from app.services.permissions import RetrievalScope


# --------------------------------------------------------------------------- #
# Permission ordering helpers
# --------------------------------------------------------------------------- #
class TestPermissionMath:
    def test_max_permission_picks_highest(self) -> None:
        assert (
            max_permission(PermissionLevel.NONE, PermissionLevel.VIEWER, PermissionLevel.MANAGER)
            == PermissionLevel.MANAGER
        )
        assert (
            max_permission(PermissionLevel.VIEWER, PermissionLevel.EDITOR) == PermissionLevel.EDITOR
        )

    def test_max_permission_empty_is_none(self) -> None:
        assert max_permission() == PermissionLevel.NONE

    def test_max_permission_all_none(self) -> None:
        assert max_permission(PermissionLevel.NONE, PermissionLevel.NONE) == PermissionLevel.NONE

    def test_permission_at_least_monotonic(self) -> None:
        assert permission_at_least(PermissionLevel.MANAGER, PermissionLevel.EDITOR)
        assert permission_at_least(PermissionLevel.EDITOR, PermissionLevel.EDITOR)
        assert permission_at_least(PermissionLevel.VIEWER, PermissionLevel.NONE)
        assert not permission_at_least(PermissionLevel.VIEWER, PermissionLevel.EDITOR)
        assert not permission_at_least(PermissionLevel.NONE, PermissionLevel.VIEWER)


# --------------------------------------------------------------------------- #
# RetrievalScope predicate construction
# --------------------------------------------------------------------------- #
def _compiled_where(scope: RetrievalScope) -> str:
    """Compile ``scope.apply(select(DocumentChunk.id))`` to a SQL string."""
    return str(scope.apply(select(DocumentChunk.id)))


class TestRetrievalScopeApply:
    def test_is_empty_semantics(self) -> None:
        oid = uuid.uuid4()
        assert RetrievalScope(org_id=oid).is_empty is True
        assert RetrievalScope(org_id=oid, all_access=True).is_empty is False
        assert RetrievalScope(org_id=oid, collection_ids={uuid.uuid4()}).is_empty is False
        assert RetrievalScope(org_id=oid, extra_document_ids={uuid.uuid4()}).is_empty is False

    def test_org_scope_is_always_applied(self) -> None:
        for scope in (
            RetrievalScope(org_id=uuid.uuid4(), all_access=True),
            RetrievalScope(org_id=uuid.uuid4(), collection_ids={uuid.uuid4()}),
            RetrievalScope(org_id=uuid.uuid4()),
        ):
            assert "document_chunks.org_id" in _compiled_where(scope)

    def test_all_access_has_no_id_filters(self) -> None:
        sql = _compiled_where(RetrievalScope(org_id=uuid.uuid4(), all_access=True))
        assert "document_chunks.org_id" in sql
        assert " IN " not in sql
        assert "IS NULL" not in sql

    def test_all_access_with_denials(self) -> None:
        scope = RetrievalScope(
            org_id=uuid.uuid4(), all_access=True, denied_document_ids={uuid.uuid4()}
        )
        sql = _compiled_where(scope)
        assert "document_chunks.document_id NOT IN" in sql

    def test_collection_scope_filters_by_collection(self) -> None:
        scope = RetrievalScope(org_id=uuid.uuid4(), collection_ids={uuid.uuid4(), uuid.uuid4()})
        sql = _compiled_where(scope)
        assert "document_chunks.collection_id IN" in sql

    def test_extra_documents_are_allowed(self) -> None:
        scope = RetrievalScope(org_id=uuid.uuid4(), extra_document_ids={uuid.uuid4()})
        sql = _compiled_where(scope)
        assert "document_chunks.document_id IN" in sql

    def test_collections_plus_denied_documents(self) -> None:
        scope = RetrievalScope(
            org_id=uuid.uuid4(),
            collection_ids={uuid.uuid4()},
            denied_document_ids={uuid.uuid4()},
        )
        sql = _compiled_where(scope)
        assert "document_chunks.collection_id IN" in sql
        assert "document_chunks.document_id NOT IN" in sql

    def test_no_access_produces_impossible_predicate(self) -> None:
        # all_access False with no collections and no extra documents => the caller can
        # see nothing, which must compile to a predicate that matches no rows.
        scope = RetrievalScope(org_id=uuid.uuid4())
        sql = _compiled_where(scope)
        assert "document_chunks.id IS NULL" in sql
