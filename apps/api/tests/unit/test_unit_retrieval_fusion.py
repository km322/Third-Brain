"""Pure-logic tests for retrieval fusion + the ``retrieve`` orchestration.

* :func:`_reciprocal_rank_fusion` is a pure function - tested directly for scoring,
  de-duplication by ``chunk_id`` and ordering.
* :func:`retrieve` is exercised with a **stubbed vector store object** and monkeypatched
  scope/embed helpers so no database, Redis or network is touched. This verifies the
  hybrid/vector-only branching and the "empty scope short-circuits before embedding"
  guarantee.
"""

from __future__ import annotations

import uuid

import pytest

import app.services.retrieval as retrieval
from app.services.permissions import RetrievalScope
from app.services.retrieval import _RRF_K, _reciprocal_rank_fusion, retrieve
from app.services.vectorstore import SearchHit


def _hit(chunk_id: uuid.UUID | None = None, *, content: str = "c") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        content=content,
        score=0.0,
    )


# --------------------------------------------------------------------------- #
# _reciprocal_rank_fusion
# --------------------------------------------------------------------------- #
class TestReciprocalRankFusion:
    def test_scores_are_normalized_to_the_unit_interval(self) -> None:
        a, b = _hit(), _hit()
        fused = _reciprocal_rank_fusion([[a, b]], top_k=10)
        # The raw RRF sum is divided by the max attainable (rank 0 in every list), so the
        # top hit is exactly 1.0 and the ranking below it is preserved but rescaled to [0, 1]
        # - never the ~0.016 raw value that read as "2% match" in the UI.
        assert fused[0].chunk_id == a.chunk_id
        assert fused[0].score == pytest.approx(1.0)
        assert fused[1].score == pytest.approx((_RRF_K + 1) / (_RRF_K + 2))
        assert all(0.0 <= h.score <= 1.0 for h in fused)

    def test_dedupes_by_chunk_id_and_sums_contributions(self) -> None:
        cid = uuid.uuid4()
        in_a = _hit(cid, content="first-seen")
        in_b = _hit(cid, content="second-seen")
        other = _hit()
        fused = _reciprocal_rank_fusion([[in_a], [other, in_b]], top_k=10)
        ids = [h.chunk_id for h in fused]
        # Appears once despite being in both lists.
        assert ids.count(cid) == 1
        winner = next(h for h in fused if h.chunk_id == cid)
        # The FIRST-seen hit object survives (setdefault semantics)...
        assert winner.content == "first-seen"
        # ...carrying the SUMMED contributions from both lists, normalized by the max a
        # two-list fusion can attain (rank 0 in both).
        raw = 1.0 / (_RRF_K + 1) + 1.0 / (_RRF_K + 2)
        max_attainable = 2 / (_RRF_K + 1)
        assert winner.score == pytest.approx(raw / max_attainable)

    def test_orders_by_fused_score_descending(self) -> None:
        shared = uuid.uuid4()
        top = _hit(shared)  # rank 0 in both lists -> highest fused score
        mid = _hit()  # rank 1 in list 1
        low = _hit()  # rank 1 in list 2
        fused = _reciprocal_rank_fusion([[top, mid], [top, low]], top_k=10)
        assert fused[0].chunk_id == shared
        scores = [h.score for h in fused]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_truncates(self) -> None:
        hits = [_hit() for _ in range(6)]
        fused = _reciprocal_rank_fusion([hits], top_k=3)
        assert len(fused) == 3
        # Kept the three best (list order == descending rank score here).
        assert [h.chunk_id for h in fused] == [h.chunk_id for h in hits[:3]]

    def test_single_list_is_a_stable_passthrough(self) -> None:
        hits = [_hit() for _ in range(4)]
        fused = _reciprocal_rank_fusion([hits], top_k=10)
        assert [h.chunk_id for h in fused] == [h.chunk_id for h in hits]

    def test_empty_inputs(self) -> None:
        assert _reciprocal_rank_fusion([], top_k=5) == []
        assert _reciprocal_rank_fusion([[]], top_k=5) == []


# --------------------------------------------------------------------------- #
# retrieve() with a stubbed store + monkeypatched scope/embed (no infra)
# --------------------------------------------------------------------------- #
class _StubStore:
    """A stand-in vector store that returns canned hits and records call args."""

    def __init__(self, vector_hits=None, keyword_hits=None) -> None:
        self._vector = vector_hits or []
        self._keyword = keyword_hits or []
        self.similarity_calls: list[int] = []
        self.keyword_calls: list[int] = []

    async def similarity_search(self, db, scope, query_embedding, top_k):
        self.similarity_calls.append(top_k)
        return list(self._vector)

    async def keyword_search(self, db, scope, query, top_k):
        self.keyword_calls.append(top_k)
        return list(self._keyword)


class _StubCtx:
    """Minimal AuthContext stand-in - retrieve() only reads .org_id here."""

    org_id = uuid.uuid4()


def _patch(monkeypatch, *, scope: RetrievalScope, store: _StubStore, embed_called):
    async def fake_scope(db, ctx, collection_ids=None):
        return scope

    async def fake_embed(db, ctx, query):
        embed_called.append(query)
        return [0.1, 0.2, 0.3], False

    monkeypatch.setattr(retrieval, "build_retrieval_scope", fake_scope)
    monkeypatch.setattr(retrieval, "get_vector_store", lambda: store)
    monkeypatch.setattr(retrieval, "_embed_query", fake_embed)


class TestRetrieveOrchestration:
    async def test_empty_scope_short_circuits_before_embedding(self, monkeypatch) -> None:
        embed_called: list[str] = []
        store = _StubStore(vector_hits=[_hit()])
        _patch(
            monkeypatch,
            scope=RetrievalScope(org_id=_StubCtx.org_id),  # is_empty
            store=store,
            embed_called=embed_called,
        )
        out = await retrieve(None, _StubCtx(), "hello")
        assert out == []
        assert embed_called == []  # never paid for an embedding
        assert store.similarity_calls == []

    async def test_non_hybrid_returns_vector_only(self, monkeypatch) -> None:
        embed_called: list[str] = []
        vhits = [_hit() for _ in range(5)]
        store = _StubStore(vector_hits=vhits, keyword_hits=[_hit()])
        _patch(
            monkeypatch,
            scope=RetrievalScope(org_id=_StubCtx.org_id, all_access=True),
            store=store,
            embed_called=embed_called,
        )
        out = await retrieve(None, _StubCtx(), "q", top_k=3, hybrid=False)
        assert [h.chunk_id for h in out] == [h.chunk_id for h in vhits]
        assert store.similarity_calls == [3]  # asked store for exactly top_k
        assert store.keyword_calls == []  # keyword search skipped
        assert embed_called == ["q"]

    async def test_hybrid_fuses_vector_and_keyword(self, monkeypatch) -> None:
        shared = uuid.uuid4()
        vhits = [_hit(shared), _hit()]
        khits = [_hit(shared), _hit()]
        store = _StubStore(vector_hits=vhits, keyword_hits=khits)
        _patch(
            monkeypatch,
            scope=RetrievalScope(org_id=_StubCtx.org_id, all_access=True),
            store=store,
            embed_called=[],
        )
        out = await retrieve(None, _StubCtx(), "q", top_k=5, hybrid=True)
        # The shared chunk, ranked #1 by both retrievers, fuses to the top.
        assert out[0].chunk_id == shared
        # Over-fetch: candidate_k = min(max(top_k*3, top_k), 50) = 15.
        assert store.similarity_calls == [15]
        assert store.keyword_calls == [15]

    async def test_hybrid_with_no_keyword_hits_returns_vector(self, monkeypatch) -> None:
        vhits = [_hit() for _ in range(4)]
        store = _StubStore(vector_hits=vhits, keyword_hits=[])
        _patch(
            monkeypatch,
            scope=RetrievalScope(org_id=_StubCtx.org_id, all_access=True),
            store=store,
            embed_called=[],
        )
        out = await retrieve(None, _StubCtx(), "q", top_k=2, hybrid=True)
        assert [h.chunk_id for h in out] == [h.chunk_id for h in vhits[:2]]

    async def test_hybrid_with_no_vector_hits_returns_keyword(self, monkeypatch) -> None:
        khits = [_hit() for _ in range(4)]
        store = _StubStore(vector_hits=[], keyword_hits=khits)
        _patch(
            monkeypatch,
            scope=RetrievalScope(org_id=_StubCtx.org_id, all_access=True),
            store=store,
            embed_called=[],
        )
        out = await retrieve(None, _StubCtx(), "q", top_k=2, hybrid=True)
        assert [h.chunk_id for h in out] == [h.chunk_id for h in khits[:2]]
