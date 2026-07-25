"""Schemas for the data-governance surface (DLP / oversharing)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.enums import SensitivityLevel, Visibility


class OversharingItem(BaseModel):
    """A document classified sensitive that is also broadly visible."""

    document_id: uuid.UUID
    title: str
    sensitivity: SensitivityLevel
    effective_visibility: Visibility
    collection_id: uuid.UUID
    collection_name: str


class SensitivitySummary(BaseModel):
    pii: int = 0
    confidential: int = 0


class OversharingReport(BaseModel):
    items: list[OversharingItem]
    summary: SensitivitySummary
    total_oversharing: int
