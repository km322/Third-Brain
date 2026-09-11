"""Curated Answers: authoritative Q&A that can be verified and surfaced above retrieval.

Permission: a collection-scoped answer is governed by the collection (VIEWER to read,
EDITOR to write, MANAGER to verify); an org-level answer is governed by its visibility
(reads) and the caller's org role (writes require EDITOR+, verify requires admin).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import (
    AuthContext,
    require_read_scope,
    require_write_scope,
    role_at_least,
)
from app.models.answer import Answer
from app.models.enums import (
    AuditAction,
    OrgRole,
    PermissionLevel,
    ResourceType,
    VerificationStatus,
)
from app.schemas.answer import AnswerCreate, AnswerRead, AnswerUpdate, AnswerVerify
from app.schemas.common import Message
from app.services.answers import answer_visibility_filters, can_read_answer
from app.services.metering import record_audit
from app.services.permissions import build_retrieval_scope, require_permission
from app.services.verification import compute_expiry, resolve_interval

router = APIRouter(prefix="/answers", tags=["answers"])


async def _get_owned(db: AsyncSession, ctx: AuthContext, answer_id: uuid.UUID) -> Answer:
    answer = await db.get(Answer, answer_id)
    if answer is None or answer.org_id != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")
    return answer


async def _require_write(db: AsyncSession, ctx: AuthContext, answer: Answer) -> None:
    """Write access: EDITOR on the collection, or org EDITOR+ for an org-level answer."""
    if answer.collection_id is not None:
        await require_permission(
            db, ctx, ResourceType.COLLECTION, answer.collection_id, PermissionLevel.EDITOR
        )
    elif not role_at_least(ctx.org_role, OrgRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires org role 'editor' or higher",
        )


@router.get("", response_model=list[AnswerRead])
async def list_answers(
    ctx: AuthContext = Depends(require_read_scope()),
    db: AsyncSession = Depends(get_db),
) -> list[AnswerRead]:
    """List answers visible to the caller, newest first.

    The visibility decision is pushed into SQL, as it is for chunk retrieval: the previous
    shape loaded every answer in the org and graded each one in Python, which cost a
    permission resolution per row and grew without bound.
    """
    scope = await build_retrieval_scope(db, ctx)
    rows = (
        (
            await db.execute(
                select(Answer)
                .where(*answer_visibility_filters(ctx, scope))
                .order_by(Answer.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [AnswerRead.model_validate(a) for a in rows]


@router.post("", response_model=AnswerRead, status_code=status.HTTP_201_CREATED)
async def create_answer(
    payload: AnswerCreate,
    ctx: AuthContext = Depends(require_write_scope()),
    db: AsyncSession = Depends(get_db),
) -> AnswerRead:
    if payload.collection_id is not None:
        await require_permission(
            db, ctx, ResourceType.COLLECTION, payload.collection_id, PermissionLevel.EDITOR
        )
    elif not role_at_least(ctx.org_role, OrgRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires org role 'editor' or higher",
        )
    answer = Answer(
        org_id=ctx.org_id,
        collection_id=payload.collection_id,
        created_by_id=ctx.user_id,
        question=payload.question,
        answer=payload.answer,
        visibility=payload.visibility,
    )
    db.add(answer)
    await db.flush()
    await record_audit(
        db, ctx, AuditAction.ANSWER_CREATED.value, resource_type="answer", resource_id=answer.id
    )
    await db.commit()
    await db.refresh(answer)
    return AnswerRead.model_validate(answer)


@router.get("/{answer_id}", response_model=AnswerRead)
async def get_answer(
    answer_id: uuid.UUID,
    ctx: AuthContext = Depends(require_read_scope()),
    db: AsyncSession = Depends(get_db),
) -> AnswerRead:
    answer = await _get_owned(db, ctx, answer_id)
    if not await can_read_answer(db, ctx, answer):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")
    return AnswerRead.model_validate(answer)


@router.patch("/{answer_id}", response_model=AnswerRead)
async def update_answer(
    answer_id: uuid.UUID,
    payload: AnswerUpdate,
    ctx: AuthContext = Depends(require_write_scope()),
    db: AsyncSession = Depends(get_db),
) -> AnswerRead:
    """Update an answer.

    Editing content invalidates verification: it must be re-reviewed.
    """
    answer = await _get_owned(db, ctx, answer_id)
    await _require_write(db, ctx, answer)
    if payload.question is not None:
        answer.question = payload.question
    if payload.answer is not None:
        answer.answer = payload.answer
    if payload.visibility is not None:
        answer.visibility = payload.visibility
    if payload.question is not None or payload.answer is not None:
        answer.verification_status = VerificationStatus.UNVERIFIED
        answer.verified_by_id = None
        answer.verified_at = None
        answer.expires_at = None
    await db.commit()
    await db.refresh(answer)
    return AnswerRead.model_validate(answer)


@router.post("/{answer_id}/verify", response_model=AnswerRead)
async def verify_answer(
    answer_id: uuid.UUID,
    payload: AnswerVerify,
    ctx: AuthContext = Depends(require_write_scope()),
    db: AsyncSession = Depends(get_db),
) -> AnswerRead:
    """Mark an answer authoritative. Requires MANAGER on its collection, or org admin."""
    answer = await _get_owned(db, ctx, answer_id)
    if answer.collection_id is not None:
        await require_permission(
            db, ctx, ResourceType.COLLECTION, answer.collection_id, PermissionLevel.MANAGER
        )
    elif not ctx.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires org admin to verify"
        )
    now = datetime.now(UTC)
    interval = resolve_interval(payload.review_interval_days)
    answer.verification_status = VerificationStatus.VERIFIED
    answer.verified_by_id = ctx.user_id
    answer.verified_at = now
    answer.review_interval_days = interval
    answer.expires_at = compute_expiry(now, interval)
    await db.commit()
    await db.refresh(answer)
    return AnswerRead.model_validate(answer)


@router.delete("/{answer_id}", response_model=Message)
async def delete_answer(
    answer_id: uuid.UUID,
    ctx: AuthContext = Depends(require_write_scope()),
    db: AsyncSession = Depends(get_db),
) -> Message:
    answer = await _get_owned(db, ctx, answer_id)
    await _require_write(db, ctx, answer)
    await record_audit(
        db, ctx, AuditAction.ANSWER_DELETED.value, resource_type="answer", resource_id=answer.id
    )
    await db.delete(answer)
    await db.commit()
    return Message(detail="Answer deleted")
