"""Schemas for multi-turn conversations."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import MessageRole
from app.schemas.common import ORMModel


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    collection_ids: list[uuid.UUID] | None = None
    web_enabled: bool = False


class ConversationRead(ORMModel):
    id: uuid.UUID
    title: str | None = None
    web_enabled: bool
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationMessageRead(ORMModel):
    id: uuid.UUID
    seq: int
    role: MessageRole
    content: str
    citations: list | None = None
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[ConversationMessageRead] = Field(default_factory=list)
