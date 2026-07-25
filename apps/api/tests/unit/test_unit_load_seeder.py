"""Pure-logic tests for the load-test corpus seeder (``loadtests.seed_corpus``).

These exercise only the seeder's offline, infra-free helpers - no locust, no database, no
network:

* the fast composed embedding, which MUST stay byte-identical to the app's deterministic
  offline provider (``_fake_embedding``) so corpus vectors and query vectors embedded at
  request time live in the same space, and
* the deterministic synthetic-text / query generators, so keyword search returns hits and
  a rerun reproduces the same corpus.

Expectations are derived from that contract and from the reference provider, not from the
seeder's own implementation.
"""

from __future__ import annotations

import math

import pytest

from app.core.config import settings
from app.services.llm.client import _fake_embedding
from loadtests.seed_corpus import (
    QUERY_COUNT,
    VOCABULARY,
    chunk_content,
    fast_embedding,
    make_queries,
    self_check_embeddings,
)

# Single-token, multi-token, repeated-token (accumulation + case folding), empty and
# whitespace-only, plus real generated chunk text.
_SAMPLE_TEXTS = [
    "single",
    "two tokens",
    "Repeated repeated REPEATED tokens tokens",
    "",
    "   ",
    "café touché naïve",
    chunk_content(0),
    chunk_content(123_457),
]


class TestFastEmbeddingMatchesProvider:
    """``fast_embedding`` must reproduce ``_fake_embedding`` byte-for-byte."""

    @pytest.mark.parametrize("text", _SAMPLE_TEXTS)
    def test_matches_reference_at_production_dim(self, text: str) -> None:
        dim = settings.EMBEDDING_DIM
        assert fast_embedding(text, dim) == _fake_embedding(text, dim)

    @pytest.mark.parametrize("dim", [1, 8, 16, 64, 384, 1536])
    def test_matches_reference_across_dims(self, dim: int) -> None:
        for text in _SAMPLE_TEXTS:
            assert fast_embedding(text, dim) == _fake_embedding(text, dim)

    def test_memo_stays_dim_correct_when_warmed(self) -> None:
        # The per-token memo caches dim-independent hash offsets, so embedding the same token
        # at a second dim after the memo is warm must still match the reference - a regression
        # guard against caching post-modulo indices.
        token = "sharedtoken"
        assert fast_embedding(token, 8) == _fake_embedding(token, 8)
        assert fast_embedding(token, 4096) == _fake_embedding(token, 4096)
        assert fast_embedding(token, 8) == _fake_embedding(token, 8)

    def test_unit_normalized(self) -> None:
        vec = fast_embedding("two tokens", settings.EMBEDDING_DIM)
        assert abs(math.sqrt(sum(v * v for v in vec)) - 1.0) < 1e-9

    def test_empty_is_non_degenerate(self) -> None:
        vec = fast_embedding("", settings.EMBEDDING_DIM)
        assert len(vec) == settings.EMBEDDING_DIM
        assert any(v != 0.0 for v in vec)
        assert all(math.isfinite(v) for v in vec)

    def test_self_check_passes_at_production_dim(self) -> None:
        # The seeder aborts at startup if this diverges; it must hold for the real dim.
        self_check_embeddings(settings.EMBEDDING_DIM)


class TestSyntheticText:
    """``chunk_content`` is a deterministic, keyword-searchable corpus generator."""

    def test_same_index_gives_same_text(self) -> None:
        assert chunk_content(42) == chunk_content(42)
        assert chunk_content(0) == chunk_content(0)
        assert chunk_content(5_000_000) == chunk_content(5_000_000)

    def test_adjacent_indices_differ(self) -> None:
        assert chunk_content(42) != chunk_content(43)

    @pytest.mark.parametrize("index", [0, 1, 42, 999, 123_457, 5_000_000])
    def test_word_count_in_range(self, index: int) -> None:
        assert 40 <= len(chunk_content(index).split()) <= 80

    @pytest.mark.parametrize("index", [0, 1, 42, 999, 5_000_000])
    def test_words_drawn_from_vocabulary(self, index: int) -> None:
        vocab = set(VOCABULARY)
        assert all(word in vocab for word in chunk_content(index).split())


class TestVocabularyAndQueries:
    def test_vocabulary_is_two_thousand_unique_words(self) -> None:
        assert len(VOCABULARY) == 2000
        assert len(set(VOCABULARY)) == 2000

    def test_queries_are_deterministic(self) -> None:
        assert make_queries() == make_queries()

    def test_query_count(self) -> None:
        assert len(make_queries()) == QUERY_COUNT

    def test_queries_are_two_to_four_vocabulary_words(self) -> None:
        vocab = set(VOCABULARY)
        for phrase in make_queries():
            words = phrase.split()
            assert 2 <= len(words) <= 4
            assert all(word in vocab for word in words)
