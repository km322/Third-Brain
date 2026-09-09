"""Curated-answer visibility + query matching.

Shared by the answers route and the search surface so both resolve "which verified
answers may this caller see, and which match their query" identically. Permission for a
collection-scoped answer flows through the same permission engine as everything else; an
org-level answer is governed by its own ``visibility``.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import or_, select
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
from app.services.permissions import effective_permission

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


async def matching_answers(
    db: AsyncSession, ctx: AuthContext, query: str, *, limit: int = 3
) -> list[Answer]:
    """Verified answers visible to ``ctx`` whose question shares a word with ``query``."""
    words = list(dict.fromkeys(w.lower() for w in _WORD_RE.findall(query)))[:8]
    if not words:
        return []
    conditions = [Answer.question.ilike(f"%{w}%") for w in words]
    candidates = (
        (
            await db.execute(
                select(Answer)
                .where(
                    Answer.org_id == ctx.org_id,
                    Answer.verification_status == VerificationStatus.VERIFIED,
                    or_(*conditions),
                )
                .order_by(Answer.updated_at.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    visible: list[Answer] = []
    perm_cache: dict[uuid.UUID, PermissionLevel] = {}
    for answer in candidates:
        if await can_read_answer(db, ctx, answer, perm_cache=perm_cache):
            visible.append(answer)
        if len(visible) >= limit:
            break
    return visible
