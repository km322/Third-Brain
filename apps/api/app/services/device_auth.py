"""Pure helpers for the CLI device-authorization flow.

Kept free of app/session state (mirroring :mod:`app.core.security`) so the code
generators and the lazy status resolution are trivially unit-testable offline.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from app.core.security import hash_api_key
from app.models.enums import DeviceAuthStatus

USER_CODE_ALPHABET = "23456789BCDFGHJKMNPQRSTVWXZ"
"""Human-typable code alphabet: no vowels (avoids spelling words) and none of the
glyph pairs people confuse when reading a terminal (0/O, 1/I/L)."""

USER_CODE_GROUP_LENGTH = 4

DEVICE_CODE_PREFIX = "tbd"
"""Device codes look like: tbd_<random>; never a tb_ API key."""


def generate_user_code() -> str:
    """A short human code in ``XXXX-XXXX`` form from the unambiguous alphabet."""
    groups = (
        "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_GROUP_LENGTH))
        for _ in range(2)
    )
    return "-".join(groups)


def normalize_user_code(code: str) -> str:
    """Canonicalise what a human typed/pasted: trim, uppercase, restore the dash.

    Strips *all* interior whitespace (tabs, non-breaking spaces from a copy/paste),
    not just ASCII spaces, so a code lifted out of a terminal still resolves.
    """
    cleaned = "".join(code.split()).upper()
    if "-" not in cleaned and len(cleaned) == 2 * USER_CODE_GROUP_LENGTH:
        cleaned = f"{cleaned[:USER_CODE_GROUP_LENGTH]}-{cleaned[USER_CODE_GROUP_LENGTH:]}"
    return cleaned


def generate_device_code() -> tuple[str, str, str]:
    """Return (full_code, prefix, sha256_hash) - same shape as ``generate_api_key``.

    Only the hash is persisted; the full code is the CLI's polling secret, shown once.
    """
    full_code = f"{DEVICE_CODE_PREFIX}_{secrets.token_urlsafe(32)}"
    return full_code, full_code[:12], hash_api_key(full_code)


def resolve_status(
    status: DeviceAuthStatus, expires_at: datetime, now: datetime
) -> DeviceAuthStatus:
    """The effective status once lazy expiry is applied.

    A row still awaiting action (pending, or approved-but-unredeemed) past ``expires_at``
    is EXPIRED - there is no background job, so every read resolves through this. Settled
    states (denied/consumed/expired) are final and never change.
    """
    if status in (DeviceAuthStatus.PENDING, DeviceAuthStatus.APPROVED) and expires_at < now:
        return DeviceAuthStatus.EXPIRED
    return status
