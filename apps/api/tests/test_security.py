"""Unit tests for :mod:`app.core.security`.

Pure and dependency-free -- these never touch a database or the network, so they always
run. They cover password hashing/verification, API-key generation + hashing, symmetric
secret encryption, and JWT creation/decoding (including expiry and wrong-signature).
"""

from __future__ import annotations

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    API_KEY_PREFIX,
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


class TestPasswords:
    """Password hashing and verification."""

    def test_hash_is_not_plaintext(self) -> None:
        """The stored hash differs from the secret and carries the ``$2`` bcrypt marker."""
        hashed = hash_password("hunter2!")
        assert hashed != "hunter2!"
        assert hashed.startswith("$2")

    def test_verify_accepts_correct_and_rejects_wrong(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True
        assert verify_password("wrong password", hashed) is False

    def test_hash_is_salted_so_two_hashes_differ(self) -> None:
        """Two hashes of the same secret differ, yet both still verify against it."""
        a = hash_password("same-password")
        b = hash_password("same-password")
        assert a != b
        assert verify_password("same-password", a)
        assert verify_password("same-password", b)


class TestApiKeys:
    """API-key generation and hashing."""

    def test_generate_shape(self) -> None:
        """The raw key, its 12-char prefix and its 64-char (sha256 hex) digest."""
        full, prefix, hashed = generate_api_key()
        assert full.startswith(f"{API_KEY_PREFIX}_live_")
        assert prefix == full[:12]
        assert len(prefix) == 12
        assert len(hashed) == 64
        assert all(c in "0123456789abcdef" for c in hashed)

    def test_environment_is_embedded(self) -> None:
        full, _, _ = generate_api_key(environment="test")
        assert full.startswith(f"{API_KEY_PREFIX}_test_")

    def test_hash_roundtrip_is_deterministic(self) -> None:
        full, _, hashed = generate_api_key()
        assert hash_api_key(full) == hashed

    def test_distinct_keys_hash_differently(self) -> None:
        full_a, _, hash_a = generate_api_key()
        full_b, _, hash_b = generate_api_key()
        assert full_a != full_b
        assert hash_a != hash_b


class TestSecretEncryption:
    """Symmetric encryption of connector secrets."""

    def test_roundtrip(self) -> None:
        plaintext = "sk-super-secret-connector-credential"
        ciphertext = encrypt_secret(plaintext)
        assert ciphertext != plaintext
        assert decrypt_secret(ciphertext) == plaintext

    def test_ciphertext_is_non_deterministic(self) -> None:
        """Fernet embeds a random IV.

        Encrypting twice therefore yields different ciphertexts, both of which decrypt
        back to the same plaintext.
        """
        a = encrypt_secret("value")
        b = encrypt_secret("value")
        assert a != b
        assert decrypt_secret(a) == decrypt_secret(b) == "value"

    def test_tampered_ciphertext_is_rejected(self) -> None:
        from cryptography.fernet import InvalidToken

        token = encrypt_secret("value")
        with pytest.raises(InvalidToken):
            decrypt_secret(token[:-2] + ("AA" if not token.endswith("AA") else "BB"))


class TestJwt:
    """JWT creation and decoding, including expiry and wrong-signature."""

    def test_access_token_carries_subject_type_and_extra(self) -> None:
        token = create_access_token("user-123", extra={"org": "org-abc"})
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["type"] == "access"
        assert payload["org"] == "org-abc"
        assert "iat" in payload and "exp" in payload

    def test_refresh_token_has_refresh_type_and_no_org(self) -> None:
        token = create_refresh_token("user-123")
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["type"] == "refresh"
        assert "org" not in payload

    def test_access_and_refresh_are_distinguishable(self) -> None:
        access = decode_token(create_access_token("u"))
        refresh = decode_token(create_refresh_token("u"))
        assert access["type"] != refresh["type"]

    def test_expired_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A negative expiry mints a token that expired a minute ago."""
        monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", -1)
        token = create_access_token("user-123")
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token)

    def test_wrong_signature_raises(self) -> None:
        forged = jwt.encode(
            {"sub": "user-123", "type": "access"},
            "a-different-secret-key-that-is-at-least-32-bytes-long",
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(jwt.InvalidSignatureError):
            decode_token(forged)

    def test_garbage_token_raises_pyjwt_error(self) -> None:
        with pytest.raises(jwt.PyJWTError):
            decode_token("not.a.jwt")
