"""Ranking-quality metrics for the retrieval benchmark harness.

Pure functions over ranked lists of document ids, with no I/O and no dependency
beyond :mod:`math`. Every function takes ``ranked`` - a retrieval-ranked list of
doc ids where rank 0 is the top hit - and (except :func:`mean`) ``relevant`` - the
set of ground-truth relevant doc ids. All functions are robust to duplicate ids in
``ranked``: the first occurrence wins and later duplicates are ignored, even though
callers are expected to pass an already de-duplicated list.

Edge-case conventions (documented so the tests can pin them down):

* An empty ``relevant`` set makes :func:`recall_at_k`, :func:`hit_rate_at_k`,
  :func:`reciprocal_rank`, :func:`average_precision` and :func:`ndcg_at_k` return
  ``0.0`` - there is nothing to recall, so an otherwise-undefined ratio is reported
  as no credit rather than raising.
* An empty ``ranked`` list, or ``k <= 0``, yields ``0.0`` from every metric.
* :func:`precision_at_k` divides by the number of *distinct* ids actually present
  in the top-``k`` prefix (which equals ``k`` once at least ``k`` distinct ids were
  ranked), so duplicate ids can never inflate the denominator.
"""

from __future__ import annotations

import math


def _dedup(ranked: list[str]) -> list[str]:
    """Return ``ranked`` with duplicate ids dropped, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for doc_id in ranked:
        if doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
    return out


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant docs that appear in the top-``k`` hits (0.0 if none relevant)."""
    if not relevant or k <= 0:
        return 0.0
    top_k = _dedup(ranked)[:k]
    hits = sum(1 for doc_id in top_k if doc_id in relevant)
    return hits / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-``k`` distinct hits that are relevant (0.0 for an empty prefix)."""
    if k <= 0:
        return 0.0
    top_k = _dedup(ranked)[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for doc_id in top_k if doc_id in relevant)
    return hits / len(top_k)


def hit_rate_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """1.0 when any relevant doc is in the top-``k`` hits, else 0.0."""
    if not relevant or k <= 0:
        return 0.0
    top_k = _dedup(ranked)[:k]
    return 1.0 if any(doc_id in relevant for doc_id in top_k) else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    """``1 / (rank + 1)`` of the first relevant hit over the whole list, else 0.0."""
    if not relevant:
        return 0.0
    for rank, doc_id in enumerate(_dedup(ranked)):
        if doc_id in relevant:
            return 1.0 / (rank + 1)
    return 0.0


def average_precision(ranked: list[str], relevant: set[str]) -> float:
    """Mean of precision@rank taken at each relevant hit, normalised by ``len(relevant)``.

    Returns 0.0 when ``relevant`` is empty. Precision is measured over the full
    ranked list; there is no ``k`` cut-off here.
    """
    if not relevant:
        return 0.0
    hits = 0
    score = 0.0
    for rank, doc_id in enumerate(_dedup(ranked)):
        if doc_id in relevant:
            hits += 1
            score += hits / (rank + 1)
    return score / len(relevant)


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Normalised DCG over the top-``k`` hits with binary gains and a log2 discount.

    ``DCG = sum(rel_i / log2(i + 2))`` for ``i`` in ``0..k-1`` with ``rel_i`` in
    ``{0, 1}``; the ideal DCG packs every relevant doc into the earliest positions.
    Returns 0.0 when ``relevant`` is empty or ``k <= 0`` (the ideal DCG is then
    zero). The ideal ranking is truncated to ``k`` positions, so a query with more
    relevant docs than ``k`` can still reach a perfect 1.0.
    """
    if not relevant or k <= 0:
        return 0.0
    top_k = _dedup(ranked)[:k]
    dcg = sum(1.0 / math.log2(i + 2) for i, doc_id in enumerate(top_k) if doc_id in relevant)
    ideal_hits = min(k, len(relevant))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def mean(values: list[float]) -> float:
    """Arithmetic mean of ``values``; 0.0 for an empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)
