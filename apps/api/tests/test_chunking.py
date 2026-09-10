"""Unit tests for :mod:`app.services.chunking`.

The window arithmetic (:func:`_windows`) is verified deterministically -- it is
independent of whether ``tiktoken`` is installed. The public :func:`chunk_text` is
verified at the level of invariants that hold under both the tiktoken and the
whitespace-fallback code paths (contiguous indices, non-empty content, bounded token
counts, sane handling of degenerate size/overlap inputs).
"""

from __future__ import annotations

from app.core.config import settings
from app.services.chunking import TextChunk, _windows, chunk_text


class TestWindows:
    """Window arithmetic - deterministic, and independent of whether tiktoken is installed."""

    def test_basic_overlap(self) -> None:
        """size=4, overlap=1 => step=3."""
        assert list(_windows(10, 4, 1)) == [(0, 4), (3, 7), (6, 10)]

    def test_no_overlap_tiles_exactly(self) -> None:
        assert list(_windows(6, 3, 0)) == [(0, 3), (3, 6)]

    def test_dense_overlap(self) -> None:
        """size=3, overlap=1 => step=2."""
        assert list(_windows(6, 3, 1)) == [(0, 3), (2, 5), (4, 6)]

    def test_single_window_when_shorter_than_size(self) -> None:
        assert list(_windows(5, 10, 2)) == [(0, 5)]

    def test_empty_length_yields_nothing(self) -> None:
        assert list(_windows(0, 4, 1)) == []

    def test_terminates_and_last_window_reaches_end(self) -> None:
        """The last window reaches the end, and windows advance monotonically."""
        windows = list(_windows(100, 10, 3))
        assert windows[0][0] == 0
        assert windows[-1][1] == 100
        for (s0, _), (s1, _) in zip(windows, windows[1:], strict=False):
            assert s1 > s0


def test_textchunk_fields() -> None:
    chunk = TextChunk(index=2, content="hello", token_count=1)
    assert (chunk.index, chunk.content, chunk.token_count) == (2, "hello", 1)


class TestChunkText:
    """Invariants of :func:`chunk_text` that hold under both backends."""

    def test_empty_and_whitespace_input_returns_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\t  ") == []
        assert chunk_text(None) == []  # type: ignore[arg-type]

    def test_short_text_is_one_chunk(self) -> None:
        chunks = chunk_text("hello world foo bar")
        assert len(chunks) == 1
        assert chunks[0].index == 0
        assert "hello" in chunks[0].content and "bar" in chunks[0].content
        assert chunks[0].token_count > 0

    def test_long_text_splits_into_contiguous_indexed_chunks(self) -> None:
        """Indices are contiguous from 0, and every chunk is non-empty and within budget."""
        text = " ".join(f"word{i}" for i in range(300))
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        assert len(chunks) > 1
        assert [c.index for c in chunks] == list(range(len(chunks)))
        for c in chunks:
            assert c.content.strip()
            assert 0 < c.token_count <= 20

    def test_overlap_is_clamped_and_does_not_hang(self) -> None:
        """``overlap >= size`` must be clamped to ``size - 1`` (step >= 1) so this terminates."""
        text = " ".join(f"tok{i}" for i in range(50))
        chunks = chunk_text(text, chunk_size=4, overlap=100)
        assert len(chunks) > 1
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_chunk_size_one_produces_many_chunks(self) -> None:
        text = " ".join(f"t{i}" for i in range(10))
        chunks = chunk_text(text, chunk_size=1, overlap=0)
        assert len(chunks) >= 10
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_defaults_come_from_settings(self) -> None:
        """A body far under the configured window collapses to a single chunk.

        Its token estimate stays within the configured chunk size.
        """
        chunks = chunk_text("a short sentence about knowledge bases")
        assert len(chunks) == 1
        assert chunks[0].token_count <= settings.CHUNK_SIZE_TOKENS
