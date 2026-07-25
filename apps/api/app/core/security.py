"""Security primitives: password hashing, JWTs, API-key + secret handling.

Kept dependency-free of the rest of the app so it is trivially unit-testable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet

from app.core.config import settings

API_KEY_PREFIX = "tb"  # keys look like: tb_live_<random>


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def _bcrypt_prehash(password: str) -> bytes:
    """SHA-256 then base64 so passwords of any length fit bcrypt's 72-byte input
    without silent truncation (the Django ``bcrypt_sha256`` approach)."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_prehash(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


async def hash_password_async(password: str) -> str:
    """Async wrapper around :func:`hash_password`.

    bcrypt at cost 12 blocks for ~50-100ms; running it in a worker thread keeps the
    event loop free during login/signup/password-change flows. Sync callers (seed
    scripts, tests) keep using :func:`hash_password` directly.
    """
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """Async wrapper around :func:`verify_password` (see :func:`hash_password_async`)."""
    return await asyncio.to_thread(verify_password, plain, hashed)


# --------------------------------------------------------------------------- #
# JWTs
# --------------------------------------------------------------------------- #
def _create_token(
    subject: str,
    token_type: Literal["access", "refresh"],
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    return _create_token(
        subject,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra,
    )


def create_refresh_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    return _create_token(
        subject,
        "refresh",
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        extra,
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# --------------------------------------------------------------------------- #
# API keys - only ever stored as a hash; the raw key is shown once.
# --------------------------------------------------------------------------- #
def generate_api_key(environment: str = "live") -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash).

    ``prefix`` is stored in the clear so we can show ``tb_live_abcd…`` in the UI.
    """
    random_part = secrets.token_urlsafe(32)
    full_key = f"{API_KEY_PREFIX}_{environment}_{random_part}"
    prefix = full_key[:12]
    return full_key, prefix, hash_api_key(full_key)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Symmetric encryption for connector credentials at rest.
# --------------------------------------------------------------------------- #
def _fernet() -> Fernet:
    # Derive a stable 32-byte Fernet key from SECRET_KEY.
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
