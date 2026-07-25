"""Pure-logic tests for :func:`app.services.ingestion.sniffed_image_mime`.

The sniff is the AUTHORIZATION basis for the public ``/files/{token}`` endpoint (bytes,
never the client-supplied mime type, decide what may be served inline), so the
signatures must identify real raster images and nothing else. WEBP needs special care:
its ``RIFF`` prefix alone also matches non-image containers (WAV, AVI).
"""

from __future__ import annotations

from app.services.ingestion import sniffed_image_mime


def _riff(format_tag: bytes) -> bytes:
    return b"RIFF" + (1234).to_bytes(4, "little") + format_tag + b"\x00" * 16


def test_raster_signatures_detected() -> None:
    assert sniffed_image_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == "image/png"
    assert sniffed_image_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "image/jpeg"
    assert sniffed_image_mime(b"GIF89a" + b"\x00" * 16) == "image/gif"
    assert sniffed_image_mime(_riff(b"WEBP")) == "image/webp"


def test_non_image_riff_containers_are_rejected() -> None:
    # A WAV or AVI relabelled image/webp must not pass: it would be base64-shipped to a
    # vision provider and served inline from the capability URL as an "image".
    assert sniffed_image_mime(_riff(b"WAVE")) is None
    assert sniffed_image_mime(_riff(b"AVI ")) is None


def test_short_and_junk_bytes_are_rejected() -> None:
    assert sniffed_image_mime(b"") is None
    assert sniffed_image_mime(b"RIFF") is None
    assert sniffed_image_mime(b"RIFF\x00\x00\x00\x00") is None
    assert sniffed_image_mime(b"plain text file") is None
