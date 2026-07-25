"""Integration: a fast smoke that the benchmark harness runs end-to-end and holds the
permission invariant.

This drives the real harness (:mod:`benchmarks.harness`) against the golden dataset on a
tiny slice -- two queries under a single retrieval config, with answers off -- so it stays
quick while still exercising the whole path: materialize a fresh benchmark org, ingest the
corpus through the real pipeline, and run permission-scoped retrieval for each asking
principal. The load-bearing checks are that a normal query returns hits and that a
permission-critical query leaks nothing: no retrieved document falls outside the asker's
ground-truth visible set. A leaked document is a permission-engine failure, not a ranking
miss, so it is a hard assertion rather than a soft quality signal.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from benchmarks.dataset import BenchmarkDataset, QuerySpec, load_dataset, validate_dataset
from benchmarks.harness import RetrievalConfig, run

pytestmark = pytest.mark.integration

# apps/api/benchmarks/golden, resolved relative to this test file (apps/api/tests/integration).
GOLDEN_DIR = pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "golden"


def _normal_query(ds: BenchmarkDataset) -> QuerySpec:
    """First query whose relevant docs are all inside the asking principal's visible set.

    Such a query is answerable for its asker, so permission-scoped retrieval should surface
    hits rather than be correctly starved by the permission engine.
    """
    for query in ds.queries:
        relevant = set(query.relevant_doc_ids)
        if relevant and relevant <= ds.visible_doc_ids(query.principal_key):
            return query
    raise AssertionError("golden dataset has no fully-visible query to smoke-test")


def _permission_critical_query(ds: BenchmarkDataset) -> QuerySpec:
    """First query with a relevant doc the asking principal is NOT entitled to read.

    The correct behavior for such a query is to retrieve nothing from the forbidden
    collection, which is exactly the leakage the smoke asserts against.
    """
    for query in ds.queries:
        if set(query.relevant_doc_ids) - ds.visible_doc_ids(query.principal_key):
            return query
    raise AssertionError("golden dataset has no permission-critical query to smoke-test")


async def test_benchmark_harness_smoke_holds_permission_invariant(db_ready) -> None:
    ds = load_dataset(str(GOLDEN_DIR))
    assert validate_dataset(ds) == [], "golden dataset must be internally consistent"

    normal = _normal_query(ds)
    blocked = _permission_critical_query(ds)

    # A tiny slice: two queries, one hybrid config, retrieval only (no RAG answers) keeps the
    # smoke fast while still ingesting the corpus and running real permission-scoped retrieval.
    slice_ds = dataclasses.replace(ds, queries=[normal, blocked])
    config = RetrievalConfig(key="hybrid_k10", hybrid=True, top_k=10)

    result = await run(slice_ds, [config], with_answers=False)

    # One run per (query, config); the harness produced the runs it was asked for.
    assert len(result.runs) == 2
    by_query = {r.query_id: r for r in result.runs}

    # A normal query (its answering docs are visible to the asker) returns some hits.
    normal_run = by_query[normal.query_id]
    assert normal_run.ranked_doc_ids, "expected retrieval hits for a normal query"

    # The hard invariant: no run may surface a document outside the asker's ground-truth
    # visible set. This is the whole point of the harness passing no collection filter.
    for query_run in result.runs:
        visible = set(result.visible_doc_ids[query_run.principal_key])
        leaked = [doc_id for doc_id in query_run.ranked_doc_ids if doc_id not in visible]
        assert leaked == [], f"permission leakage in {query_run.query_id}: {leaked}"

    # Specifically for the permission-critical query: the relevant-but-forbidden document is
    # in the corpus and matches the query, yet the permission engine keeps it out of results.
    blocked_run = by_query[blocked.query_id]
    blocked_visible = set(result.visible_doc_ids[blocked.principal_key])
    forbidden = set(blocked.relevant_doc_ids) - blocked_visible
    assert forbidden, "expected the permission-critical query to have a forbidden relevant doc"
    assert not (forbidden & set(blocked_run.ranked_doc_ids))
