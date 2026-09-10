"""Unit: device-auth pure helpers - code generators and the lazy status resolution."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_api_key
from app.models.enums import DeviceAuthStatus
from app.services.device_auth import (
    USER_CODE_ALPHABET,
    generate_device_code,
    generate_user_code,
    normalize_user_code,
    resolve_status,
)


def test_user_code_shape_and_alphabet() -> None:
    for _ in range(50):
        code = generate_user_code()
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code), code
        for ch in code.replace("-", ""):
            assert ch in USER_CODE_ALPHABET


def test_user_code_alphabet_is_unambiguous() -> None:
    """No glyphs a human could misread from a terminal (0/O, 1/I/L) and no vowels."""
    assert set("01OIL") & set(USER_CODE_ALPHABET) == set()
    assert set("AEIOU") & set(USER_CODE_ALPHABET) == set()


def test_user_codes_are_random() -> None:
    codes = {generate_user_code() for _ in range(100)}
    assert len(codes) == 100


def test_normalize_user_code() -> None:
    """Spacing and casing are normalised to the canonical form; anything else passes through
    cleaned rather than being rejected here (the lookup simply misses)."""
    assert normalize_user_code("  bcdf-2345 ") == "BCDF-2345"
    assert normalize_user_code("bcdf2345") == "BCDF-2345"
    assert normalize_user_code("BCDF 2345") == "BCDF-2345"
    assert normalize_user_code("nope") == "NOPE"


def test_device_code_shape_matches_api_key_generator() -> None:
    """A device code shares the API-key generator's shape but wears its own ``tbd_`` prefix, so
    it never routes down the API-key auth path."""
    full, prefix, hashed = generate_device_code()
    assert full.startswith("tbd_")
    assert not full.startswith("tb_")
    assert prefix == full[:12]
    assert hashed == hash_api_key(full)
    assert generate_device_code()[0] != full


@pytest.mark.parametrize(
    ("status", "expired", "expected"),
    [
        (DeviceAuthStatus.PENDING, False, DeviceAuthStatus.PENDING),
        (DeviceAuthStatus.PENDING, True, DeviceAuthStatus.EXPIRED),
        (DeviceAuthStatus.APPROVED, False, DeviceAuthStatus.APPROVED),
        (DeviceAuthStatus.APPROVED, True, DeviceAuthStatus.EXPIRED),
        (DeviceAuthStatus.DENIED, True, DeviceAuthStatus.DENIED),
        (DeviceAuthStatus.CONSUMED, True, DeviceAuthStatus.CONSUMED),
        (DeviceAuthStatus.EXPIRED, False, DeviceAuthStatus.EXPIRED),
    ],
)
def test_resolve_status_lazy_expiry(
    status: DeviceAuthStatus, expired: bool, expected: DeviceAuthStatus
) -> None:
    """Only rows still awaiting action (pending/approved) expire; settled states are final."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    delta = timedelta(minutes=-1 if expired else 1)
    assert resolve_status(status, now + delta, now) == expected
