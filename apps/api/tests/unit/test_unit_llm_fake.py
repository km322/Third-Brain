"""Pure-logic tests for the deterministic offline LLM provider.

No provider key is configured in the test environment (conftest pins ``EMBEDDING_PROVIDER
= "fake"``), so :func:`embed_texts` / :func:`complete` / :func:`stream_complete` take their
offline branch and never touch the network. These tests assert determinism, the correct
embedding dimension (``settings.EMBEDDING_DIM``) and stable output across calls.
"""

from __future__ import annotations

import math

from app.core.config import settings
from app.services.llm import ChatMessage, complete, embed_texts, stream_complete
from app.services.llm.client import (
    _estimate_tokens,
    _fake_embedding,
    _offline_completion,
)


class TestEstimateTokens:
    """:func:`_estimate_tokens`."""

    def test_floor_is_one(self) -> None:
        assert _estimate_tokens([""]) == 1
        assert _estimate_tokens(["ab"]) == 1

    def test_sums_across_texts(self) -> None:
        assert _estimate_tokens(["x" * 40, "y" * 40]) == 20


class TestFakeEmbedding:
    """:func:`_fake_embedding` - deterministic pseudo-embeddings."""

    def test_dimension_matches_requested(self) -> None:
        for dim in (8, 128, settings.EMBEDDING_DIM):
            assert len(_fake_embedding("hello world", dim)) == dim

    def test_is_unit_normalized(self) -> None:
        vec = _fake_embedding("some representative text", 256)
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-9

    def test_deterministic_across_calls(self) -> None:
        a = _fake_embedding("repeatable input", 64)
        b = _fake_embedding("repeatable input", 64)
        assert a == b

    def test_case_insensitive(self) -> None:
        assert _fake_embedding("Hello World", 64) == _fake_embedding("hello world", 64)

    def test_different_text_gives_different_vector(self) -> None:
        assert _fake_embedding("alpha", 64) != _fake_embedding("beta", 64)

    def test_empty_string_is_handled(self) -> None:
        """The empty string still yields a full-length, non-degenerate, finite vector."""
        vec = _fake_embedding("", 32)
        assert len(vec) == 32
        assert any(v != 0.0 for v in vec)
        assert all(math.isfinite(v) for v in vec)


class TestOfflineCompletion:
    """:func:`_offline_completion`."""

    def test_grounds_on_last_user_message(self) -> None:
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="prior"),
            ChatMessage(role="user", content="LATEST QUESTION"),
        ]
        text = _offline_completion(messages)
        assert "[offline model]" in text
        assert "LATEST QUESTION" in text

    def test_no_user_message_still_produces_text(self) -> None:
        text = _offline_completion([ChatMessage(role="system", content="only system")])
        assert "[offline model]" in text

    def test_deterministic(self) -> None:
        messages = [ChatMessage(role="user", content="ask me")]
        assert _offline_completion(messages) == _offline_completion(messages)

    def test_rag_prompt_is_answered_not_echoed(self) -> None:
        """For a retrieval-grounded prompt (the real ``rag._assemble`` output) the stub must
        answer *from* the passages with ``[n]`` citations and the actual question - never
        surfacing the ``<passage>`` delimiters or the internal 'Answer the question…'
        instructions to the user.

        So the assertions come in two halves: no internal scaffolding leaks to the user, and
        what remains reads as a grounded, cited answer to the real question.
        """
        import uuid

        from app.services import rag
        from app.services.vectorstore import SearchHit

        hits = [
            SearchHit(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                collection_id=uuid.uuid4(),
                content="The CEO's total compensation is 480,000 dollars plus equity.",
                score=1.0,
                document_title="Compensation Bands",
            )
        ]
        question = "What is the CEO's compensation?"
        _selected, messages = rag._assemble(question, hits)

        text = _offline_completion(messages)

        assert "<passage" not in text
        assert "Answer the question using only" not in text
        assert "bracket numbers" not in text
        assert "[offline model]" in text
        assert question in text
        assert "[1]" in text
        assert "Compensation Bands" in text
        assert "The CEO's total compensation is 480,000 dollars plus equity." in text

    def test_rag_extract_never_truncates_mid_word(self) -> None:
        """A long passage is clipped to a short lead, but always at a word boundary -
        the leaked stub used to cut off mid-word (e.g. '…Senior').

        The clipped text must therefore be a prefix of the source that ends on a whole-word
        boundary.
        """
        import uuid

        from app.services import rag
        from app.services.vectorstore import SearchHit

        body = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 20
        hits = [
            SearchHit(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                collection_id=uuid.uuid4(),
                content=body.strip(),
                score=1.0,
                document_title="Phonetics",
            )
        ]
        _selected, messages = rag._assemble("recite the alphabet", hits)

        text = _offline_completion(messages)
        line = next(ln for ln in text.splitlines() if ln.startswith("[1]"))
        extract = line.split(" - ", 1)[1]
        assert extract.endswith("…"), "a long passage should be clipped"
        normalized = " ".join(body.split())
        clipped = extract[:-1]
        assert normalized.startswith(clipped)
        assert normalized[len(clipped)] == " "


class TestEmbedTexts:
    """:func:`embed_texts` - async offline path."""

    async def test_shape_provider_and_dimension(self) -> None:
        result = await embed_texts(["one", "two", "three"])
        assert result.provider == "offline"
        assert result.model == "offline"
        assert len(result.vectors) == 3
        assert all(len(v) == settings.EMBEDDING_DIM for v in result.vectors)
        assert result.tokens > 0

    async def test_stable_across_calls(self) -> None:
        a = await embed_texts(["deterministic"])
        b = await embed_texts(["deterministic"])
        assert a.vectors == b.vectors

    async def test_matches_the_pure_helper(self) -> None:
        result = await embed_texts(["compare me"])
        assert result.vectors[0] == _fake_embedding("compare me", settings.EMBEDDING_DIM)


class TestComplete:
    """:func:`complete` + :func:`stream_complete` - async offline path."""

    async def test_returns_offline_stub_with_metadata(self) -> None:
        messages = [ChatMessage(role="user", content="hi there")]
        result = await complete(messages)
        assert result.provider == "offline"
        assert result.model == "offline"
        assert result.finish_reason == "stop"
        assert "[offline model]" in result.text
        assert "hi there" in result.text
        assert result.tokens_in >= 1
        assert result.tokens_out >= 1

    async def test_deterministic(self) -> None:
        messages = [ChatMessage(role="user", content="same prompt")]
        a = await complete(messages)
        b = await complete(messages)
        assert a.text == b.text

    async def test_stream_reconstructs_the_same_text(self) -> None:
        """Draining the stream yields the same non-empty text as the single-shot call."""
        messages = [ChatMessage(role="user", content="stream this")]
        streamed = "".join([d async for d in stream_complete(messages)])
        single = await complete(messages)
        assert streamed.strip() == single.text.strip()
        assert streamed
