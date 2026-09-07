"""Schemas for the entity (NER) index."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.enums import EntityKind


class EntityRead(BaseModel):
    id: uuid.UUID
    kind: EntityKind
    name: str
    # Number of documents the caller can see that mention this entity.
    document_count: int
