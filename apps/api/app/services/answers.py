"""Curated-answer visibility + query matching.

Shared by the answers route and the search surface so both resolve "which verified
answers may this caller see, and which match their query" identically. Permission for a
collection-scoped answer flows through the same permission engine as everything else; an
org-level answer is governed by its own ``visibility``.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.answer import Answer
from app.models.enums import (
    PermissionLevel,
    ResourceType,
    VerificationStatus,
    Visibility,
    permission_at_least,
)
from app.services.permissions import RetrievalScope, build_retrieval_scope, effective_permission

_WORD_RE = re.compile(r"[A-Za-z0-9]{4,}")


async def can_read_answer(
    db: AsyncSession,
    ctx: AuthContext,
    answer: Answer,
    *,
    perm_cache: dict[uuid.UUID, PermissionLevel] | None = None,
) -> bool:
    """Whether ``ctx`` may read ``answer`` (org-scoped; caller already org-checked).

    ``perm_cache`` memoises the collection lookup across a single request, for callers
    grading a list of answers. Answers cluster into far fewer collections than there are
    answers, and ``effective_permission`` costs 2-3 queries each time, so without it a
    list route spends a query storm re-deriving the same collection's permission. It is a
    cache of this engine's own answer, never a reimplementation of it - the grading logic
    below stays the single source of truth. Safe within a request: grants cannot change
    underneath it.
    """
    if ctx.is_admin or (ctx.user_id is not None and answer.created_by_id == ctx.user_id):
        return True
    if answer.collection_id is not None:
        if perm_cache is not None and answer.collection_id in perm_cache:
            perm = perm_cache[answer.collection_id]
        else:
            perm = await effective_permission(
                db, ctx, ResourceType.COLLECTION, answer.collection_id
            )
            if perm_cache is not None:
                perm_cache[answer.collection_id] = perm
        return permission_at_least(perm, PermissionLevel.VIEWER)
    return answer.visibility in (Visibility.ORG, Visibility.PUBLIC)


def answer_visibility_filters(ctx: AuthContext, scope: RetrievalScope) -> list[ColumnElement[bool]]:
    """SQL predicate restricting ``Answer`` to what ``ctx`` may read.

    The set form of :func:`can_read_answer`, so a list route can push the visibility
    decision into the query instead of loading every answer in the org and grading each
    one in Python. The two must agree exactly - the differential test in
    ``tests/integration/test_answers.py`` asserts they select the same rows across an
    admin/owner/viewer/private/team/org matrix.

    The collection branch reuses ``scope.collection_ids``, which
    :func:`build_retrieval_scope` already resolves as the collections the caller can read
    at >= VIEWER, mirroring ``effective_permission``. Document-level members of the scope
    are irrelevant here: an answer hangs off a collection or off nothing.

    The branches, in order: ``scope.all_access`` is the org owner/admin, exactly as
    :func:`can_read_answer` short-circuits; an org-level answer is governed by its own
    visibility; and your own answer is readable wherever it lives.
    """
    filters: list[ColumnElement[bool]] = [Answer.org_id == scope.org_id]
    if scope.all_access:
        return filters
    allow: list[ColumnElement[bool]] = [
        and_(
            Answer.collection_id.is_(None),
            Answer.visibility.in_([Visibility.ORG, Visibility.PUBLIC]),
        )
    ]
    if ctx.user_id is not None:
        allow.append(Answer.created_by_id == ctx.user_id)
    if scope.collection_ids:
        allow.append(Answer.collection_id.in_(scope.collection_ids))
    filters.append(or_(*allow))
    return filters


async def matching_answers(
    db: AsyncSession, ctx: AuthContext, query: str, *, limit: int = 3
) -> list[Answer]:
    """Verified answers visible to ``ctx`` whose question shares a word with ``query``.

    Visibility is filtered in SQL *before* the limit. Taking the newest 25 and then dropping
    the ones the caller cannot read would return nothing to someone whose only visible answer
    ranks 26th, even though a match exists.
    """
    words = list(dict.fromkeys(w.lower() for w in _WORD_RE.findall(query)))[:8]
    if not words:
        return []
    conditions = [Answer.question.ilike(f"%{w}%") for w in words]
    scope = await build_retrieval_scope(db, ctx)
    return list(
        (
            await db.execute(
                select(Answer)
                .where(
                    Answer.verification_status == VerificationStatus.VERIFIED,
                    or_(*conditions),
                    *answer_visibility_filters(ctx, scope),
                )
                .order_by(Answer.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
