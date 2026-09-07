from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ConnectorPurpose, ConnectorType

if TYPE_CHECKING:
    pass


class Connector(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A configured LLM provider endpoint for embeddings/completions.

    ``type`` selects the provider (OpenAI-compatible, Anthropic, or Google Gemini - see
    :class:`app.models.enums.ConnectorType`). Credentials are encrypted at rest (see
    app.core.security.encrypt_secret). ``config`` holds non-secret provider options
    (base_url, api_version, region…).
    """

    __tablename__ = "connectors"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[ConnectorType] = mapped_column(
        Enum(ConnectorType, native_enum=False, length=32), nullable=False
    )
    purpose: Mapped[ConnectorPurpose] = mapped_column(
        Enum(ConnectorPurpose, native_enum=False, length=32), nullable=False
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    encrypted_credentials: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Connector {self.name} {self.type}/{self.purpose}>"
