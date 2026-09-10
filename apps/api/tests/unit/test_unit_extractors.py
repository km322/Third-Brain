"""Pure-logic tests for text extraction format detection.

A recognized MIME type is authoritative and must win over a misleading filename extension
(otherwise a plain-text body titled ``report.pdf`` is handed to the PDF parser and fails),
while an absent/unrecognized MIME falls back to the extension. Binary blobs that decode to
mostly replacement characters must fail rather than be indexed as mojibake.
"""

from __future__ import annotations

import pytest

from app.services.extractors import _normalize_pdf_text, extract_text


class TestMimeTakesPriorityOverExtension:
    def test_text_plain_titled_pdf_is_decoded_as_text(self) -> None:
        """The exact reported bug: POST /documents/text stores mime=text/plain, but a title
        ending in ".pdf" used to route to the PDF extractor and raise."""
        out = extract_text(b"hello world", mime_type="text/plain", filename="Q3 report.pdf")
        assert out == "hello world"

    def test_text_plain_titled_docx_is_decoded_as_text(self) -> None:
        out = extract_text(b"just text", mime_type="text/plain", filename="notes.docx")
        assert out == "just text"

    def test_markdown_mime_titled_pdf_is_decoded(self) -> None:
        out = extract_text(b"# Heading", mime_type="text/markdown", filename="x.pdf")
        assert "# Heading" in out


class TestExtensionFallbackWhenMimeUnknown:
    def test_html_extension_used_when_mime_is_generic(self) -> None:
        """Stripped tags prove the HTML was actually parsed rather than passed through."""
        html = b"<html><body><p>Hi there</p></body></html>"
        out = extract_text(html, mime_type="application/octet-stream", filename="page.html")
        assert "Hi there" in out
        assert "<p>" not in out

    def test_html_extension_used_when_mime_absent(self) -> None:
        html = b"<html><body><p>Body text</p></body></html>"
        out = extract_text(html, mime_type=None, filename="page.htm")
        assert "Body text" in out


class TestBinaryMojibakeIsRejected:
    def test_binary_blob_without_usable_mime_raises(self) -> None:
        """A PNG header + random bytes: decoding as UTF-8 yields mostly replacement chars."""
        blob = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
        with pytest.raises(ValueError):
            extract_text(blob, mime_type="application/octet-stream", filename="logo.png")

    def test_valid_utf8_text_is_not_rejected(self) -> None:
        out = extract_text("café - résumé\nsecond line".encode(), mime_type="text/plain")
        assert "café" in out


class TestPdfTextNormalization:
    """pypdf output from Google-Docs-style exports carries a lone-space line between
    soft-wrapped words and multi-space gaps between every word; normalization must
    collapse both while leaving real (single-newline) line structure intact."""

    def test_soft_wrapped_words_are_rejoined(self) -> None:
        raw = "Have\n \nyou\n \nbeen\n \nto\n \nany\n \nYC\n \nevents?"
        assert _normalize_pdf_text(raw) == "Have you been to any YC events?"

    def test_multi_space_word_gaps_collapse(self) -> None:
        raw = "1.  Founder  Profile  \nAre  you  a  technical  founder?  Yes  "
        assert _normalize_pdf_text(raw) == "1. Founder Profile\nAre you a technical founder? Yes"

    def test_real_line_breaks_survive(self) -> None:
        raw = "Question one? Answer\nQuestion two? Answer"
        assert _normalize_pdf_text(raw) == raw
