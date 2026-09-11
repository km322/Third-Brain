"""First-principles tests for the benchmark ranking metrics (``benchmarks.metrics``).

Every expected value here is worked out by hand from the textbook definition of the
metric, not read back from the implementation. Where a closed form is unavoidable
(nDCG), the expected value is written as the textbook formula using ``math.log2``,
so a bug in :mod:`benchmarks.metrics` cannot silently bless itself.

The running example is ``ranked = [a, b, c, d]`` with ``relevant = {b, d}`` (b at
rank 1, d at rank 3, both zero-indexed).
"""

from __future__ import annotations

import math

import pytest

from benchmarks.metrics import (
    average_precision,
    hit_rate_at_k,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

RANKED = ["a", "b", "c", "d"]
RELEVANT = {"b", "d"}


class TestRecallAtK:
    def test_worked_example_at_2(self) -> None:
        """top-2 = [a, b]; one of the two relevant docs is present -> 1/2."""
        assert recall_at_k(RANKED, RELEVANT, 2) == 0.5

    def test_all_relevant_found_by_k4(self) -> None:
        assert recall_at_k(RANKED, RELEVANT, 4) == 1.0

    def test_no_relevant_in_top1(self) -> None:
        """top-1 = [a]; neither b nor d present -> 0/2."""
        assert recall_at_k(RANKED, RELEVANT, 1) == 0.0

    def test_k_larger_than_list_is_fine(self) -> None:
        assert recall_at_k(["a", "b"], {"a", "b"}, 10) == 1.0

    def test_empty_relevant_is_zero(self) -> None:
        assert recall_at_k(RANKED, set(), 3) == 0.0

    def test_empty_ranked_is_zero(self) -> None:
        assert recall_at_k([], RELEVANT, 3) == 0.0

    def test_duplicates_do_not_double_count(self) -> None:
        """de-dup -> [b, x]; only b of the two relevant docs is present -> 1/2.

        A naive impl that counted the repeated b would wrongly report 1.0.
        """
        assert recall_at_k(["b", "b", "x"], RELEVANT, 2) == 0.5


class TestPrecisionAtK:
    def test_worked_example_at_2(self) -> None:
        """top-2 = [a, b]; 1 relevant of 2 items -> 1/2."""
        assert precision_at_k(RANKED, RELEVANT, 2) == 0.5

    def test_worked_example_at_4(self) -> None:
        """top-4 = [a, b, c, d]; 2 relevant of 4 -> 2/4."""
        assert precision_at_k(RANKED, RELEVANT, 4) == 0.5

    def test_no_relevant_in_top1(self) -> None:
        assert precision_at_k(RANKED, RELEVANT, 1) == 0.0

    def test_denominator_counts_distinct_items(self) -> None:
        """de-dup -> [a, x]; top-3 prefix has 2 distinct items, 1 relevant -> 1/2."""
        assert precision_at_k(["a", "a", "x"], {"a"}, 3) == 0.5

    def test_k_zero_is_zero(self) -> None:
        assert precision_at_k(RANKED, RELEVANT, 0) == 0.0

    def test_empty_ranked_is_zero(self) -> None:
        assert precision_at_k([], RELEVANT, 3) == 0.0

    def test_empty_relevant_is_zero(self) -> None:
        assert precision_at_k(RANKED, set(), 2) == 0.0


class TestHitRateAtK:
    def test_miss_at_1(self) -> None:
        assert hit_rate_at_k(RANKED, RELEVANT, 1) == 0.0

    def test_hit_at_2(self) -> None:
        assert hit_rate_at_k(RANKED, RELEVANT, 2) == 1.0

    def test_empty_relevant_is_zero(self) -> None:
        assert hit_rate_at_k(RANKED, set(), 4) == 0.0

    def test_empty_ranked_is_zero(self) -> None:
        assert hit_rate_at_k([], RELEVANT, 4) == 0.0

    def test_k_zero_is_zero(self) -> None:
        assert hit_rate_at_k(RANKED, RELEVANT, 0) == 0.0


class TestReciprocalRank:
    def test_first_relevant_at_rank1(self) -> None:
        """first relevant (b) sits at index 1 -> 1/(1+1)."""
        assert reciprocal_rank(RANKED, RELEVANT) == 0.5

    def test_first_relevant_at_rank0(self) -> None:
        assert reciprocal_rank(["b", "a"], {"b"}) == 1.0

    def test_no_relevant_retrieved(self) -> None:
        assert reciprocal_rank(["a", "c"], RELEVANT) == 0.0

    def test_empty_relevant_is_zero(self) -> None:
        assert reciprocal_rank(RANKED, set()) == 0.0

    def test_duplicates_before_first_hit_are_collapsed(self) -> None:
        """de-dup -> [x, d]; first relevant (d) at index 1 -> 1/2."""
        assert reciprocal_rank(["x", "x", "d"], {"d"}) == 0.5


class TestAveragePrecision:
    def test_worked_example(self) -> None:
        """relevant hits at ranks 1 and 3 -> precisions 1/2 and 2/4; mean over |relevant|=2."""
        expected = (1 / 2 + 2 / 4) / 2
        assert average_precision(RANKED, RELEVANT) == pytest.approx(expected)
        assert average_precision(RANKED, RELEVANT) == 0.5

    def test_perfect_ranking_is_one(self) -> None:
        """both relevant docs at the top: precisions 1/1 and 2/2 -> (1 + 1)/2."""
        assert average_precision(["b", "d", "a", "c"], RELEVANT) == 1.0

    def test_single_relevant_deep_in_list(self) -> None:
        """only relevant doc (b) at index 2 -> precision 1/3, normalised by |relevant|=1."""
        assert average_precision(["a", "c", "b"], {"b"}) == pytest.approx(1 / 3)

    def test_no_relevant_retrieved(self) -> None:
        assert average_precision(["a", "c"], RELEVANT) == 0.0

    def test_empty_relevant_is_zero(self) -> None:
        assert average_precision(RANKED, set()) == 0.0

    def test_empty_ranked_is_zero(self) -> None:
        assert average_precision([], RELEVANT) == 0.0


class TestNdcgAtK:
    def test_worked_example_at_4(self) -> None:
        """DCG: b at pos 1 -> 1/log2(3), d at pos 3 -> 1/log2(5).

        IDCG: two relevant packed first -> 1/log2(2) + 1/log2(3).
        """
        expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 / math.log2(2) + 1 / math.log2(3))
        assert ndcg_at_k(RANKED, RELEVANT, 4) == pytest.approx(expected)

    def test_perfect_ranking_is_one(self) -> None:
        assert ndcg_at_k(["b", "d", "a", "c"], RELEVANT, 4) == pytest.approx(1.0)

    def test_no_relevant_in_top1(self) -> None:
        """DCG = 0 (a is not relevant); IDCG = 1/log2(2) = 1 -> 0.0."""
        assert ndcg_at_k(RANKED, RELEVANT, 1) == 0.0

    def test_ideal_is_truncated_to_k(self) -> None:
        """k=1 with three relevant docs: IDCG uses a single ideal position, so a single top
        hit yields a perfect 1.0 rather than being penalised.
        """
        assert ndcg_at_k(["b", "d"], {"b", "d", "e"}, 1) == pytest.approx(1.0)

    def test_empty_relevant_is_zero(self) -> None:
        assert ndcg_at_k(RANKED, set(), 4) == 0.0

    def test_k_zero_is_zero(self) -> None:
        assert ndcg_at_k(RANKED, RELEVANT, 0) == 0.0


class TestMean:
    def test_empty_is_zero(self) -> None:
        assert mean([]) == 0.0

    def test_simple_average(self) -> None:
        assert mean([1.0, 2.0, 3.0]) == 2.0

    def test_single_value(self) -> None:
        assert mean([0.5]) == 0.5
