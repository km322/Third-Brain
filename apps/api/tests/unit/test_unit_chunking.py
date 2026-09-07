"""Pure-logic tests for :mod:`app.services.chunking`.

The window arithmetic is deterministic and tiktoken-independent. ``chunk_text`` is
verified at the level of invariants that hold under BOTH the tiktoken and the
whitespace-fallback paths: contiguous indices, non-empty content, bounded token counts,
honored size/overlap, and graceful handling of empty/tiny/huge/unicode inputs.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.chunking import (
    TextChunk,
    _chunk_with_whitespace,
    _windows,
    chunk_text,
)


# --------------------------------------------------------------------------- #
# _windows - pure integer arithmetic
# --------------------------------------------------------------------------- #
class TestWindows:
    def test_overlap_step(self) -> None:
        # size=4, overlap=1 => step=3.
        assert list(_windows(10, 4, 1)) == [(0, 4), (3, 7), (6, 10)]

    def test_zero_overlap_tiles_exactly(self) -> None:
        assert list(_windows(6, 3, 0)) == [(0, 3), (3, 6)]

    def test_single_window_when_shorter_than_size(self) -> None:
        assert list(_windows(5, 10, 2)) == [(0, 5)]

    def test_empty_length_yields_nothing(self) -> None:
        assert list(_windows(0, 4, 1)) == []

    def test_terminates_when_overlap_would_stall(self) -> None:
        # step is floored at 1 even if overlap >= size, so this must terminate.
        windows = list(_windows(5, 3, 3))
        assert windows  # non-empty
        assert windows[-1][1] == 5
        starts = [s for s, _ in windows]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)  # strictly advancing

    def test_last_window_reaches_end(self) -> None:
        windows = list(_windows(100, 10, 3))
        assert windows[0][0] == 0
        assert windows[-1][1] == 100
        assert all(0 <= s < e <= 100 for s, e in windows)


# --------------------------------------------------------------------------- #
# TextChunk dataclass
# --------------------------------------------------------------------------- #
def test_textchunk_fields() -> None:
    chunk = TextChunk(index=2, content="hi", token_count=1)
    assert (chunk.index, chunk.content, chunk.token_count) == (2, "hi", 1)


# --------------------------------------------------------------------------- #
# chunk_text - invariants under both backends
# --------------------------------------------------------------------------- #
class TestChunkText:
    def test_empty_inputs_return_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\t ") == []
        assert chunk_text(None) == []  # type: ignore[arg-type]

    def test_short_text_is_one_chunk(self) -> None:
        chunks = chunk_text("hello world foo bar")
        assert len(chunks) == 1
        assert chunks[0].index == 0
        assert "hello" in chunks[0].content and "bar" in chunks[0].content
        assert chunks[0].token_count > 0

    def test_long_text_splits_contiguously_and_honors_size(self) -> None:
        text = " ".join(f"word{i}" for i in range(300))
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        assert len(chunks) > 1
        assert [c.index for c in chunks] == list(range(len(chunks)))
        for c in chunks:
            assert c.content.strip()
            assert 0 < c.token_count <= 20

    def test_overlap_ge_size_is_clamped_and_terminates(self) -> None:
        text = " ".join(f"tok{i}" for i in range(50))
        chunks = chunk_text(text, chunk_size=4, overlap=100)
        assert len(chunks) > 1
        assert [c.index for c in chunks] == list(range(len(chunks)))
        for c in chunks:
            assert 0 < c.token_count <= 4

    def test_size_one_produces_at_least_one_chunk_per_token(self) -> None:
        text = " ".join(f"t{i}" for i in range(10))
        chunks = chunk_text(text, chunk_size=1, overlap=0)
        assert len(chunks) >= 10
        assert [c.index for c in chunks] == list(range(len(chunks)))
        for c in chunks:
            assert c.token_count == 1

    def test_defaults_come_from_settings(self) -> None:
        chunks = chunk_text("a short sentence about knowledge bases")
        assert len(chunks) == 1
        assert chunks[0].token_count <= settings.CHUNK_SIZE_TOKENS

    def test_unicode_is_preserved(self) -> None:
        text = "café über naïve 日本語 emoji 🚀 résumé " * 20
        chunks = chunk_text(text, chunk_size=8, overlap=2)
        assert chunks
        assert [c.index for c in chunks] == list(range(len(chunks)))
        joined = " ".join(c.content for c in chunks)
        # Round-trips through encode/decode without corrupting characters.
        assert "café" in joined
        assert "日本語" in joined
        assert "🚀" in joined

    def test_multibyte_boundaries_are_not_corrupted(self) -> None:
        # A long run of CJK text (no whitespace) forces multi-byte characters to straddle
        # tiktoken window boundaries. The decoder must not emit U+FFFD replacement chars,
        # and every original character must survive somewhere in the chunk set.
        text = "服务和路由配置管理系统的设计与实现原理详解" * 40
        chunks = chunk_text(text, chunk_size=16, overlap=4)
        assert len(chunks) > 1
        joined = "".join(c.content for c in chunks)
        assert "�" not in joined
        assert set(text) <= set(joined)

    def test_huge_input_does_not_hang_and_stays_bounded(self) -> None:
        text = " ".join(f"w{i}" for i in range(5000))
        chunks = chunk_text(text, chunk_size=50, overlap=10)
        assert len(chunks) > 10
        assert [c.index for c in chunks] == list(range(len(chunks)))
        assert all(0 < c.token_count <= 50 for c in chunks)


# --------------------------------------------------------------------------- #
# Natural-boundary requirements: chunks end at paragraph/sentence end points and
# no information is cut off. These invariants hold under BOTH backends.
# --------------------------------------------------------------------------- #
class TestNaturalBoundaries:
    def test_sentences_are_never_cut(self) -> None:
        text = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(40))
        chunks = chunk_text(text, chunk_size=25, overlap=5)
        assert len(chunks) > 1
        for c in chunks:
            assert c.content.endswith(".")  # every chunk closes at a sentence end
        joined = " ".join(c.content for c in chunks)
        for i in range(40):
            # Every sentence survives intact, exactly once (no loss, no duplication).
            assert joined.count(f"Sentence number {i} talks about topic {i}.") == 1

    def test_paragraphs_that_fit_pack_into_one_verbatim_chunk(self) -> None:
        text = "First paragraph here.\n\nSecond paragraph here."
        chunks = chunk_text(text, chunk_size=100, overlap=0)
        assert len(chunks) == 1
        assert chunks[0].content == text

    def test_split_lands_on_the_paragraph_boundary(self) -> None:
        paragraph = " ".join(f"Alpha beta gamma {i}." for i in range(8))
        text = paragraph + "\n\n" + paragraph
        chunks = chunk_text(text, chunk_size=60, overlap=0)
        # One paragraph fits the budget, two do not: the cut falls exactly between them.
        assert [c.content for c in chunks] == [paragraph, paragraph]

    def test_oversized_unpunctuated_run_stays_bounded(self) -> None:
        # No sentence boundary exists, so the fallback windows must still cap size.
        text = " ".join(f"w{i}" for i in range(400))
        chunks = chunk_text(text, chunk_size=50, overlap=10)
        assert len(chunks) > 1
        assert all(0 < c.token_count <= 50 for c in chunks)

    def test_chunks_close_near_target_not_at_the_ceiling(self) -> None:
        # With a big ceiling and a small target, chunks close at the first paragraph
        # boundary past the target instead of packing on toward the ceiling.
        paragraph = " ".join(f"Item {i} is described right here now." for i in range(2))
        text = "\n\n".join(paragraph for _ in range(12))
        chunks = chunk_text(text, chunk_size=200, overlap=0, target=30)
        assert len(chunks) > 2
        for c in chunks:
            assert c.content.endswith(".")
            assert c.token_count <= 60  # far below the 200 ceiling

    def test_heading_starts_a_new_chunk_once_buffer_is_substantial(self) -> None:
        body = " ".join(f"Sentence {i} of this section body." for i in range(6))
        text = f"## Section One\n\n{body}\n\n## Section Two\n\n{body}"
        chunks = chunk_text(text, chunk_size=500, overlap=0, target=40)
        assert len(chunks) == 2
        assert chunks[0].content.startswith("## Section One")
        assert chunks[1].content.startswith("## Section Two")

    def test_tiny_sections_are_not_fragmented_by_headings(self) -> None:
        # A heading only forces a break once the buffer holds half the target, so
        # heading-dense text with tiny sections still packs into one chunk.
        text = "## A\n\nTwo words.\n\n## B\n\nThree more words."
        chunks = chunk_text(text, chunk_size=500, overlap=0, target=40)
        assert len(chunks) == 1

    def test_mixed_document_loses_nothing(self) -> None:
        text = (
            "Heading line\n\n"
            + " ".join(f"Body sentence {i} explains detail {i}." for i in range(30))
            + "\n\nClosing paragraph. It has two sentences."
        )
        chunks = chunk_text(text, chunk_size=30, overlap=0)
        joined = "\n".join(c.content for c in chunks)
        assert "Heading line" in joined
        for i in range(30):
            assert f"Body sentence {i} explains detail {i}." in joined
        assert "Closing paragraph." in joined
        assert "It has two sentences." in joined


# --------------------------------------------------------------------------- #
# Whitespace fallback path (exercised directly, independent of tiktoken)
# --------------------------------------------------------------------------- #
class TestWhitespaceFallback:
    def test_word_windows_reindex_after_dropping_empties(self) -> None:
        chunks = _chunk_with_whitespace("alpha beta gamma delta", 2, 0)
        assert [c.index for c in chunks] == list(range(len(chunks)))
        assert chunks[0].content == "alpha beta"
        assert chunks[0].token_count == 2

    def test_empty_string_yields_no_chunks(self) -> None:
        assert _chunk_with_whitespace("", 4, 1) == []

    def test_paragraph_text_is_preserved_verbatim(self) -> None:
        # Paragraphs that fit the budget are packed as-is (original spacing intact),
        # joined by the natural paragraph separator - nothing is rewritten or lost.
        chunks = _chunk_with_whitespace("one   two\n\nthree\tfour", 10, 0)
        assert len(chunks) == 1
        assert chunks[0].content == "one   two\n\nthree\tfour"
        assert chunks[0].token_count == 4
