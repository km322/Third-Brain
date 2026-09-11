"""Device authorization: the "connect from your terminal, approve in the browser" flow.

A CLI POSTs to ``/device-auth`` (unauthenticated, org-less) and receives a short human
``user_code`` plus a long ``device_code`` secret. An org admin opens ``/activate``, reviews
the request and approves it, which mints an :class:`~app.models.api_key.ApiKey`; the CLI's
next poll of ``/device-auth/token`` redeems the key's plaintext exactly once.

Only the SHA-256 hash of the device code is stored. Between approval and redemption the
minted key's plaintext is held Fernet-encrypted in ``encrypted_secret`` and nulled the
moment it is handed over.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DeviceAuthStatus


class DeviceAuthorization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One CLI sign-in attempt awaiting (or past) an admin's browser approval.

    ``uq_device_auth_user_code_pending`` is partial because the human code only needs to be
    unambiguous while an approval is pending; settled rows release it (non-native enums
    persist member NAMES, hence 'PENDING').
    """

    __tablename__ = "device_authorizations"
    __table_args__ = (
        Index(
            "uq_device_auth_user_code_pending",
            "user_code",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    """Null until approved: the start call is unauthenticated, so the org is only known once
    an admin approves and binds the request to their organization."""
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_code: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    device_code_prefix: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    hashed_device_code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    requested_scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[DeviceAuthStatus] = mapped_column(
        Enum(DeviceAuthStatus, native_enum=False, length=16),
        default=DeviceAuthStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    encrypted_secret: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    """Fernet-encrypted plaintext of the minted API key, held only between approval and the
    one-shot redemption on /device-auth/token (nulled when consumed or expired)."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeviceAuthorization {self.user_code} {self.status}>"
