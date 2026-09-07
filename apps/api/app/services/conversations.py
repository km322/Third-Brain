"""Multi-turn conversation persistence for the assistant.

Conversations are per-user and org-scoped. History is loaded as ``ChatMessage`` turns for
the RAG prompt; each retrieval still runs through the permission engine, so history never
widens what the caller can see.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import MessageRole
from app.services.llm import ChatMessage

_HISTORY_TURNS = 8


async def get_owned_conversation(
    db: AsyncSession, ctx: AuthContext, conversation_id: uuid.UUID
) -> Conversation | None:
    """Fetch a conversation the caller owns (org + user scoped), else None.

    Strictly owner-only: there is NO org-admin carve-out (a member's assistant history is
    private - an admin, or an ADMIN-role API key, must not read/append/delete it), and a keyed
    caller with no acting user (``user_id is None``) owns nothing, so it can never match a
    conversation - including a NULL-owner row.
    """
    if ctx.user_id is None:
        return None
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.org_id != ctx.org_id or conv.user_id != ctx.user_id:
        return None
    return conv


async def history_messages(
    db: AsyncSession, conversation_id: uuid.UUID, *, limit: int = _HISTORY_TURNS
) -> list[ChatMessage]:
    """The last ``limit`` turns of a conversation, oldest-first, as ChatMessages."""
    rows = (
        (
            await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.seq.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [ChatMessage(role=m.role.value, content=m.content) for m in reversed(rows)]


async def append_message(
    db: AsyncSession,
    conv: Conversation,
    role: MessageRole,
    content: str,
    *,
    citations: list | None = None,
) -> ConversationMessage:
    """Append a message to a conversation (assigns the next seq). The caller commits."""
    next_seq = (
        await db.execute(
            select(func.coalesce(func.max(ConversationMessage.seq), 0) + 1).where(
                ConversationMessage.conversation_id == conv.id
            )
        )
    ).scalar_one()
    message = ConversationMessage(
        org_id=conv.org_id,
        conversation_id=conv.id,
        seq=next_seq,
        role=role,
        content=content,
        citations=citations,
    )
    db.add(message)
    conv.last_message_at = datetime.now(UTC)
    return message
