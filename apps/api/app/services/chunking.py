"""Boundary-aware, token-budgeted text chunking for embedding.

Chunks end at natural end points and prefer the STRONGEST nearby boundary: a chunk
closes before a heading once it holds a substantial amount of text, closes at a
paragraph or sentence boundary once it reaches the preferred ``CHUNK_TARGET_TOKENS``
size, and is force-closed only at the ``CHUNK_SIZE_TOKENS`` hard ceiling (embedding
models have real input limits). Whole paragraphs are packed together, an oversized
paragraph is packed sentence-by-sentence, and a sentence is never cut in half. Only a
single sentence that alone exceeds the ceiling (minified or unpunctuated text) falls
back to overlapping fixed token windows so pathological inputs stay bounded; natural
chunks carry no overlap because nothing is cut mid-thought.

Token sizes are measured with ``tiktoken`` so they line up with what the embedding
model actually sees. If tiktoken cannot be loaded (not installed, offline model
download blocked, …) we transparently fall back to a whitespace/word approximation of
the same logic, so the ingestion pipeline never hard-fails on chunking.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ENCODER_CACHE: dict[str, object] = {}
"""Cache encoders per model name; ``False`` means "tiktoken unavailable, use fallback"."""

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
"""A blank (possibly whitespace-only) line separates paragraphs."""

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
"""End-of-sentence punctuation (Latin or CJK) followed by whitespace."""

_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
"""A Markdown heading opens a new document section - the strongest break point."""


@dataclass
class TextChunk:
    """One chunk ready to be embedded."""

    index: int
    content: str
    token_count: int


def _get_encoder(model: str | None):
    """Return a tiktoken encoder for ``model`` (cached), or ``None`` if unavailable."""
    cache_key = model or "__default__"
    if cache_key in _ENCODER_CACHE:
        cached = _ENCODER_CACHE[cache_key]
        return cached or None
    encoder = None
    try:
        import tiktoken

        if model:
            try:
                encoder = tiktoken.encoding_for_model(model)
            except KeyError:
                encoder = tiktoken.get_encoding("cl100k_base")
        else:
            encoder = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # pragma: no cover - depends on tiktoken availability
        logger.warning("tiktoken unavailable (%s); using whitespace chunker", exc)
        encoder = None
    _ENCODER_CACHE[cache_key] = encoder or False
    return encoder


def _windows(length: int, size: int, overlap: int):
    """Yield (start, end) index windows over ``length`` items."""
    step = max(1, size - overlap)
    start = 0
    while start < length:
        end = min(start + size, length)
        yield start, end
        if end >= length:
            break
        start += step


def _decode_window(encoder, window: list[int]) -> str:
    """Decode a token window without corrupting multi-byte characters.

    cl100k tokens are byte-level, so a CJK/emoji character can straddle a window boundary.
    ``encoder.decode`` would turn the split bytes into U+FFFD replacement chars and persist
    that mojibake into the chunk (embedded, keyword-indexed and shown as a citation).
    Decoding the raw bytes with ``errors="ignore"`` instead drops the incomplete leading/
    trailing byte fragments cleanly; with a non-zero overlap the boundary character is
    always fully present in the neighbouring window, so nothing is lost from the index.
    """
    return encoder.decode_bytes(window).decode("utf-8", errors="ignore")


def _boundary_pack(
    text: str,
    size: int,
    target: int,
    count: Callable[[str], int],
    window_split: Callable[[str], list[str]],
) -> list[str]:
    """Pack paragraphs/sentences into chunk contents of at most ``size`` tokens,
    preferring to close each chunk at the best nearby boundary.

    ``target`` is the preferred chunk size: once the buffer reaches it, the chunk
    closes at the next paragraph or sentence boundary rather than packing on toward
    the ceiling. A heading is a stronger boundary still - the chunk closes before one
    as soon as the buffer holds half the target, so a section is never glued to the
    tail of the previous one just because there was room left.

    Units are kept verbatim (paragraph text is never rewritten) and joined with their
    natural separator - ``\\n\\n`` between paragraphs, a space between the sentences of
    a paragraph too large to keep whole. ``window_split`` handles the one case with no
    natural cut left: a single sentence that alone exceeds the ceiling.

    The running total is an upper bound on the joined chunk's real token count (BPE
    merges across a join can only shrink it), so the ``size`` ceiling always holds.

    When a paragraph is broken into sentences its first sentence keeps the paragraph's
    boundary strength, so a heading-led paragraph still closes the previous chunk.
    """
    contents: list[str] = []
    buf = ""
    buf_tokens = 0
    heading_threshold = max(1, target // 2)

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf.strip():
            contents.append(buf.strip())
        buf, buf_tokens = "", 0

    def add(unit: str, unit_tokens: int, sep: str, threshold: int) -> None:
        nonlocal buf, buf_tokens
        if buf:
            sep_tokens = count(sep)
            if buf_tokens >= threshold or buf_tokens + sep_tokens + unit_tokens > size:
                flush()
            else:
                buf += sep + unit
                buf_tokens += sep_tokens + unit_tokens
                return
        buf, buf_tokens = unit, unit_tokens

    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        threshold = heading_threshold if _HEADING_RE.match(paragraph) else target
        paragraph_tokens = count(paragraph)
        if paragraph_tokens <= size:
            add(paragraph, paragraph_tokens, "\n\n", threshold)
            continue
        sentence_threshold = threshold
        for sentence in _SENTENCE_SPLIT_RE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence_tokens = count(sentence)
            if sentence_tokens <= size:
                add(sentence, sentence_tokens, " ", sentence_threshold)
            else:
                flush()
                contents.extend(window_split(sentence))
            sentence_threshold = target
    flush()
    return contents


def _chunk_with_tiktoken(
    text: str, encoder, size: int, overlap: int, target: int
) -> list[TextChunk]:
    def count(piece: str) -> int:
        return len(encoder.encode(piece))

    def window_split(piece: str) -> list[str]:
        token_ids = encoder.encode(piece)
        decoded = (
            _decode_window(encoder, token_ids[start:end]).strip()
            for start, end in _windows(len(token_ids), size, overlap)
        )
        return [content for content in decoded if content]

    contents = _boundary_pack(text, size, target, count, window_split)
    return [
        TextChunk(index=i, content=content, token_count=count(content))
        for i, content in enumerate(contents)
    ]


def _chunk_with_whitespace(
    text: str, size: int, overlap: int, target: int | None = None
) -> list[TextChunk]:
    """Approximate the same logic using words (~1 word ≈ 1 token for English prose)."""

    def count(piece: str) -> int:
        return len(piece.split())

    def window_split(piece: str) -> list[str]:
        words = piece.split()
        joined = (
            " ".join(words[start:end]).strip() for start, end in _windows(len(words), size, overlap)
        )
        return [content for content in joined if content]

    contents = _boundary_pack(
        text, size, target if target is not None else size, count, window_split
    )
    return [
        TextChunk(index=i, content=content, token_count=count(content))
        for i, content in enumerate(contents)
    ]


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    target: int | None = None,
    model: str | None = None,
) -> list[TextChunk]:
    """Split ``text`` into boundary-aware chunks of at most ``chunk_size`` tokens.

    Chunks close at the best nearby boundary: before a heading once half the
    ``target`` is buffered, at a paragraph or sentence boundary once ``target`` is
    reached, and at the ``chunk_size`` ceiling at the latest. No sentence is ever cut
    unless it alone exceeds the ceiling, in which case that sentence (only) is split
    into overlapping token windows. ``chunk_size`` / ``target`` / ``overlap`` default
    to the configured ``CHUNK_SIZE_TOKENS`` / ``CHUNK_TARGET_TOKENS`` /
    ``CHUNK_OVERLAP_TOKENS`` (``overlap`` applies only to the oversized-sentence
    fallback). ``model`` selects the tiktoken encoding to count against (defaults to a
    cl100k base encoding).
    """
    text = (text or "").strip()
    if not text:
        return []

    size = chunk_size or settings.CHUNK_SIZE_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS if overlap is None else overlap
    target = target or settings.CHUNK_TARGET_TOKENS
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))
    target = max(1, min(target, size))

    encoder = _get_encoder(model)
    if encoder is not None:
        try:
            return _chunk_with_tiktoken(text, encoder, size, overlap, target)
        except Exception as exc:  # pragma: no cover
            logger.warning("tiktoken chunking failed (%s); falling back", exc)
    return _chunk_with_whitespace(text, size, overlap, target)
