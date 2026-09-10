"""Conversation management for the multi-turn assistant.

Conversations require a user session (an API key with no acting user has no owner). The
actual chat turns are driven through ``POST /search/chat`` with a ``conversation_id``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AuthContext, get_session_context
from app.models.conversation import Conversation
from app.schemas.common import Message
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
)
from app.services.conversations import get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    ctx: AuthContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationRead]:
    rows = (
        (
            await db.execute(
                select(Conversation)
                .where(Conversation.org_id == ctx.org_id, Conversation.user_id == ctx.user_id)
                .order_by(
                    Conversation.last_message_at.desc().nullslast(),
                    Conversation.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [ConversationRead.model_validate(c) for c in rows]


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    ctx: AuthContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationRead:
    conv = Conversation(
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        title=payload.title,
        collection_ids=[str(c) for c in payload.collection_ids] if payload.collection_ids else None,
        web_enabled=payload.web_enabled,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationRead.model_validate(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: AuthContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    """Fetch a conversation with its messages.

    The ``messages`` relationship is ordered by seq (see the model).
    """
    conv = await get_owned_conversation(db, ctx, conversation_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    await db.refresh(conv, attribute_names=["messages"])
    return ConversationDetail.model_validate(conv)


@router.delete("/{conversation_id}", response_model=Message)
async def delete_conversation(
    conversation_id: uuid.UUID,
    ctx: AuthContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_db),
) -> Message:
    conv = await get_owned_conversation(db, ctx, conversation_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return Message(detail="Conversation deleted")
