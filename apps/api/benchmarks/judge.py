"""Answer-quality scoring for the benchmark harness.

Given a :class:`~benchmarks.harness.BenchmarkRun` (produced by driving the real RAG
service), :func:`score_answers` grades every generated answer along four axes:

* ``citation_precision`` / ``citation_recall`` - do the passages the answer cited come
  from the documents that genuinely answer the query? Citations are chunk ids; each is
  mapped to its document and compared against the query's relevant documents, narrowed
  to what the asking principal could actually retrieve (an answer can only cite what
  permission let it see).
* ``keyword_coverage`` - a cheap, offline correctness proxy: the fraction of the query's
  ``reference_keywords`` that appear (whole-word-ish, case-insensitive) in the answer.
* ``llm_faithful`` / ``llm_correct`` - an LLM judge scores groundedness against the
  retrieved context and correctness against the reference answer. These run ONLY when a
  real provider is configured; offline (the deterministic stub) they are ``None``.

The module is import-safe and offline-safe: importing it touches no infrastructure, and
with no live provider it does no network I/O. Only synthetic answer text, reconstructed
context, and reference answers are ever sent to the judge - never secrets or keys.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm import ChatMessage, complete

if TYPE_CHECKING:
    from benchmarks.dataset import BenchmarkDataset
    from benchmarks.harness import BenchmarkRun, QueryRun

logger = get_logger(__name__)

# Concurrency cap for the LLM judge so a large query set never opens an unbounded number
# of provider connections at once.
_JUDGE_CONCURRENCY = 4

_JUDGE_SYSTEM = (
    "You are a strict evaluation judge for a retrieval-augmented answer. Score two "
    "independent qualities, each on a 0.0 to 1.0 scale:\n"
    "- faithfulness: how fully the answer is grounded in the provided context passages, "
    "with no claims that go beyond them (1.0 = every claim is supported, 0.0 = "
    "unsupported or contradicted).\n"
    "- correctness: how well the answer matches the reference answer in substance "
    "(1.0 = fully matches, 0.0 = wrong or missing).\n"
    "Respond with ONLY a compact JSON object of the form "
    '{"faithfulness": <0..1>, "correctness": <0..1>}. Use null for correctness when no '
    "reference answer is provided. Do not add any commentary."
)


@dataclass
class AnswerScores:
    """Per-answer quality scores for one ``(query, config)`` pair."""

    query_id: str
    config_key: str
    citation_precision: float
    citation_recall: float
    keyword_coverage: float
    llm_faithful: float | None
    llm_correct: float | None


def _provider_is_live() -> bool:
    """Mirror the LLM client's offline check: a live provider needs a real key and a
    non-stub embedding provider."""
    if settings.EMBEDDING_PROVIDER in ("fake", "offline"):
        return False
    return bool(settings.OPENAI_API_KEY)


def _chunk_to_doc_map(qr: QueryRun) -> dict[str, str]:
    """Map each retrieved chunk id to its dataset document id.

    ``retrieved_chunk_ids`` are in rank order and ``ranked_doc_ids`` is that same list
    mapped to documents and de-duplicated (first occurrence wins). When each document
    contributes a single chunk - the golden dataset's shape - the two lists are aligned
    one-to-one and the correspondence is exact. When a document produced several
    retrieved chunks the de-duplication collapses entries and only the aligned prefix is
    recoverable, so :func:`zip` (which stops at the shorter list) yields a best-effort
    map and any unmapped citation is simply not counted.
    """
    return dict(zip(qr.retrieved_chunk_ids, qr.ranked_doc_ids, strict=False))


def _cited_docs(qr: QueryRun) -> list[str]:
    """Document ids cited by the answer, de-duplicated in citation order."""
    chunk_to_doc = _chunk_to_doc_map(qr)
    ordered: list[str] = []
    for chunk_id in qr.citations:
        doc_id = chunk_to_doc.get(chunk_id)
        if doc_id is not None and doc_id not in ordered:
            ordered.append(doc_id)
    return ordered


def _visible_docs(ds: BenchmarkDataset, run: BenchmarkRun, principal_key: str) -> set[str]:
    """Ground-truth visible doc ids for a principal, from the run's declared map with a
    fallback to the dataset."""
    vmap = getattr(run, "visible_doc_ids", None) or {}
    if principal_key in vmap:
        return set(vmap[principal_key])
    try:
        return ds.visible_doc_ids(principal_key)
    except KeyError:
        return set()


def _keyword_present(keyword: str, text_lower: str) -> bool:
    """True when ``keyword`` appears in ``text_lower`` bounded by non-alphanumeric edges."""
    kw = keyword.strip().lower()
    if not kw:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
    return re.search(pattern, text_lower) is not None


def _keyword_coverage(keywords: list[str] | None, answer: str) -> float:
    """Fraction of ``keywords`` present in ``answer``; 0.0 when there are no keywords."""
    if not keywords:
        return 0.0
    text_lower = answer.lower()
    hits = sum(1 for kw in keywords if _keyword_present(kw, text_lower))
    return hits / len(keywords)


def _coerce_score(value: object) -> float | None:
    """Coerce a judge-returned value to a float clamped to ``[0, 1]``, or ``None``."""
    if value is None:
        return None
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def _parse_scores(text: str) -> tuple[float | None, float | None]:
    """Extract ``(faithfulness, correctness)`` from the judge's JSON reply, defensively."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return _coerce_score(data.get("faithfulness")), _coerce_score(data.get("correctness"))


def _judge_user(answer: str, context: str, reference: str | None) -> str:
    return (
        "Context passages the answer was allowed to use:\n"
        f"{context or '(no context passages available)'}\n\n"
        "Answer to grade:\n"
        f"{answer}\n\n"
        "Reference answer:\n"
        f"{reference or '(no reference answer provided)'}"
    )


async def _judge_one(
    answer: str, context: str, reference: str | None
) -> tuple[float | None, float | None]:
    """Ask the judge model for faithfulness (vs context) and correctness (vs reference).

    A score is returned as ``None`` when its basis is missing (no context for
    faithfulness, no reference for correctness) or when the reply cannot be parsed - a
    provider hiccup falls back to the offline stub whose non-JSON text simply parses to
    ``None`` rather than a spurious number.
    """
    messages = [
        ChatMessage(role="system", content=_JUDGE_SYSTEM),
        ChatMessage(role="user", content=_judge_user(answer, context, reference)),
    ]
    result = await complete(messages, temperature=0.0, max_tokens=200)
    faithful, correct = _parse_scores(result.text)
    return (faithful if context else None, correct if reference else None)


async def _run_llm_judges(
    items: list[tuple[tuple[str, str], str, str, str | None]],
) -> dict[tuple[str, str], tuple[float | None, float | None]]:
    """Judge every item under a concurrency cap, keyed by ``(query_id, config_key)``."""
    sem = asyncio.Semaphore(_JUDGE_CONCURRENCY)
    results: dict[tuple[str, str], tuple[float | None, float | None]] = {}

    async def worker(key: tuple[str, str], answer: str, context: str, reference: str | None):
        async with sem:
            results[key] = await _judge_one(answer, context, reference)

    await asyncio.gather(*(worker(k, a, c, r) for k, a, c, r in items))
    return results


def _run_async(make_coro):
    """Run an async workload from sync code, tolerating an already-running event loop.

    ``make_coro`` is a zero-argument callable that builds the coroutine, so a retry in a
    separate thread never re-awaits an exhausted coroutine.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())

    box: dict = {}
    error: dict = {}

    def _worker():
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller thread
            error["exc"] = exc

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return box.get("value")


def score_answers(ds: BenchmarkDataset, run: BenchmarkRun) -> list[AnswerScores]:
    """Score every answered query in ``run`` against the dataset's ground truth.

    Citation and keyword metrics are always computed. The LLM judge runs only when the
    run used a live provider (``run.provider_mode == "live"``) and settings confirm one
    is configured; otherwise ``llm_faithful`` / ``llm_correct`` stay ``None``.
    """
    queries = {q.query_id: q for q in ds.queries}
    doc_content = {d.doc_id: d.content for d in ds.documents}
    use_llm = getattr(run, "provider_mode", "offline") == "live" and _provider_is_live()

    scores: list[AnswerScores] = []
    scores_by_key: dict[tuple[str, str], AnswerScores] = {}
    llm_items: list[tuple[tuple[str, str], str, str, str | None]] = []

    for qr in run.runs:
        if qr.answer is None:
            continue
        query = queries.get(qr.query_id)
        if query is None:
            continue

        visible = _visible_docs(ds, run, qr.principal_key)
        relevant = set(query.relevant_doc_ids) & visible

        cited_docs = _cited_docs(qr)
        cited_set = set(cited_docs)
        cited_relevant = cited_set & relevant
        precision = len(cited_relevant) / len(cited_set) if cited_set else 0.0
        recall = len(cited_relevant) / len(relevant) if relevant else 0.0

        coverage = _keyword_coverage(query.reference_keywords, qr.answer)

        score = AnswerScores(
            query_id=qr.query_id,
            config_key=qr.config_key,
            citation_precision=precision,
            citation_recall=recall,
            keyword_coverage=coverage,
            llm_faithful=None,
            llm_correct=None,
        )
        scores.append(score)
        scores_by_key[(qr.query_id, qr.config_key)] = score

        if use_llm:
            context = "\n\n".join(
                doc_content[doc_id] for doc_id in cited_docs if doc_content.get(doc_id)
            )
            reference = query.reference_answer
            if context or reference:
                llm_items.append(((qr.query_id, qr.config_key), qr.answer, context, reference))

    if use_llm and llm_items:
        logger.info("answer_judge", judged=len(llm_items), mode="live")
        judged = _run_async(lambda: _run_llm_judges(llm_items))
        for key, (faithful, correct) in judged.items():
            score = scores_by_key.get(key)
            if score is not None:
                score.llm_faithful = faithful
                score.llm_correct = correct

    return scores
