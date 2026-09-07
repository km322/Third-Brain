"""CLI entrypoint for the Third Brain benchmark harness.

``python -m benchmarks.run_benchmark`` materializes a golden dataset into a throwaway
``benchmark-*`` org, drives the real retrieval and RAG services over it, then scores the
results and renders a report. It is the glue that turns the pieces (dataset, harness,
metrics, judge, perf, report) into one command.

Retrieval quality is measured against the asking principal's IN-SCOPE relevant set - the
labelled relevant docs intersected with the docs that principal is entitled to see - so
permission filtering is never scored as a retrieval miss. The complementary permission
check is stricter: any ranked doc OUTSIDE the caller's ground-truth visible set is a leak,
and a single leak fails the run (exit code 1). Queries whose entire relevant set is out of
the asker's scope carry no in-scope target, so they are excluded from the quality averages
and validated by the leakage check alone.

Usage::

    python -m benchmarks.run_benchmark [--dataset golden|<dir>] [--configs default|sweep]
        [--answers/--no-answers] [--out benchmarks/.report]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys

from app.core.logging import get_logger
from benchmarks import harness, report
from benchmarks.dataset import BenchmarkDataset, load_dataset, validate_dataset
from benchmarks.harness import BenchmarkRun, RetrievalConfig
from benchmarks.judge import score_answers
from benchmarks.metrics import (
    average_precision,
    hit_rate_at_k,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from benchmarks.perf import measure_stage_latency
from benchmarks.report import (
    ConfigMetrics,
    LeakageReport,
    QueryDiagnostic,
    RetrievalReport,
)

logger = get_logger(__name__)

_KS = [1, 3, 5, 10]
_HIT_KS = [1, 5]
_ADMIN_ROLES = ("owner", "admin")


def _build_configs(mode: str) -> list[RetrievalConfig]:
    """Build the retrieval configs to benchmark.

    ``"default"`` is a single hybrid config at top_k=8; ``"sweep"`` crosses hybrid on/off
    with top_k in {1, 3, 5, 10} for eight configs.
    """
    if mode == "default":
        return [RetrievalConfig(key="hybrid_k8", hybrid=True, top_k=8)]
    configs: list[RetrievalConfig] = []
    for hybrid in (True, False):
        label = "hybrid" if hybrid else "vector"
        for k in (1, 3, 5, 10):
            configs.append(RetrievalConfig(key=f"{label}_k{k}", hybrid=hybrid, top_k=k))
    return configs


def _primary_config_key(configs: list[RetrievalConfig]) -> str:
    """The config featured in the headline quality table: the hybrid config with the largest
    top_k (so recall@10 is meaningful), or the widest config if none are hybrid."""
    pool = [c for c in configs if c.hybrid] or configs
    return max(pool, key=lambda c: c.top_k).key


def _in_scope_relevant(ds: BenchmarkDataset, run: BenchmarkRun) -> dict[str, set[str]]:
    """Per-query relevant doc ids intersected with the asking principal's visible set."""
    vmap = {key: set(docs) for key, docs in run.visible_doc_ids.items()}
    inscope: dict[str, set[str]] = {}
    for query in ds.queries:
        visible = vmap.get(query.principal_key, set())
        inscope[query.query_id] = set(query.relevant_doc_ids) & visible
    return inscope


def _config_metrics(
    run: BenchmarkRun, config: RetrievalConfig, inscope: dict[str, set[str]]
) -> ConfigMetrics:
    """Average every ranking metric for one config over its scored queries."""
    recalls: dict[int, list[float]] = {k: [] for k in _KS}
    precisions: dict[int, list[float]] = {k: [] for k in _KS}
    hits: dict[int, list[float]] = {k: [] for k in _HIT_KS}
    rr: list[float] = []
    ap: list[float] = []
    ndcg: list[float] = []
    latencies: list[float] = []
    n_scored = 0

    for qr in run.runs:
        if qr.config_key != config.key:
            continue
        latencies.append(qr.total_latency_ms)
        relevant = inscope.get(qr.query_id, set())
        if not relevant:
            continue
        n_scored += 1
        ranked = qr.ranked_doc_ids
        for k in _KS:
            recalls[k].append(recall_at_k(ranked, relevant, k))
            precisions[k].append(precision_at_k(ranked, relevant, k))
        for k in _HIT_KS:
            hits[k].append(hit_rate_at_k(ranked, relevant, k))
        rr.append(reciprocal_rank(ranked, relevant))
        ap.append(average_precision(ranked, relevant))
        ndcg.append(ndcg_at_k(ranked, relevant, 10))

    return ConfigMetrics(
        config_key=config.key,
        hybrid=config.hybrid,
        top_k=config.top_k,
        recall_at={k: mean(recalls[k]) for k in _KS},
        precision_at={k: mean(precisions[k]) for k in _KS},
        hit_rate_at={k: mean(hits[k]) for k in _HIT_KS},
        mrr=mean(rr),
        map=mean(ap),
        ndcg_at_10=mean(ndcg),
        median_latency_ms=statistics.median(latencies) if latencies else 0.0,
        n_scored=n_scored,
    )


def _leakage(run: BenchmarkRun) -> LeakageReport:
    """Count ranked docs that fall outside the asking principal's ground-truth visible set."""
    vmap = {key: set(docs) for key, docs in run.visible_doc_ids.items()}
    total_leaks = 0
    total_ranked = 0
    leaks: list[dict] = []
    for qr in run.runs:
        visible = vmap.get(qr.principal_key, set())
        for doc_id in qr.ranked_doc_ids:
            total_ranked += 1
            if doc_id not in visible:
                total_leaks += 1
                leaks.append(
                    {
                        "query_id": qr.query_id,
                        "config_key": qr.config_key,
                        "principal_key": qr.principal_key,
                        "doc_id": doc_id,
                    }
                )
    leak_rate = total_leaks / total_ranked if total_ranked else 0.0
    return LeakageReport(
        total_leaks=total_leaks, total_ranked=total_ranked, leak_rate=leak_rate, leaks=leaks
    )


def _weakest_queries(
    ds: BenchmarkDataset,
    run: BenchmarkRun,
    inscope: dict[str, set[str]],
    primary_key: str,
) -> list[QueryDiagnostic]:
    """The five lowest recall@5 scored queries under the primary config."""
    queries = {q.query_id: q for q in ds.queries}
    diagnostics: list[QueryDiagnostic] = []
    for qr in run.runs:
        if qr.config_key != primary_key:
            continue
        relevant = inscope.get(qr.query_id, set())
        if not relevant:
            continue
        diagnostics.append(
            QueryDiagnostic(
                query_id=qr.query_id,
                config_key=qr.config_key,
                principal_key=qr.principal_key,
                text=queries[qr.query_id].text,
                in_scope_relevant=sorted(relevant),
                retrieved=qr.ranked_doc_ids[:5],
                recall_at_5=recall_at_k(qr.ranked_doc_ids, relevant, 5),
            )
        )
    diagnostics.sort(key=lambda d: (d.recall_at_5, d.query_id))
    return diagnostics[:5]


def _consistency_note(ds: BenchmarkDataset, run: BenchmarkRun) -> str:
    """A cross-principal sanity line: every admin/owner should see at least as much as all
    other principals combined."""
    vmap = {key: set(docs) for key, docs in run.visible_doc_ids.items()}
    admins = [p.key for p in ds.principals if p.role in _ADMIN_ROLES]
    if not admins:
        return "no owner/admin principal in the dataset to check against."
    everyone: set[str] = set()
    for principal in ds.principals:
        everyone |= vmap.get(principal.key, set())
    inconsistent = [key for key in admins if not (vmap.get(key, set()) >= everyone)]
    admin_list = ", ".join(admins)
    if not inconsistent:
        return (
            f"admin/owner principals ({admin_list}) each see a superset of every other "
            "principal's visible set (consistent)."
        )
    return (
        f"admin/owner principals ({', '.join(inconsistent)}) do NOT see a superset of every "
        "other principal's visible set - inspect the dataset labels or the permission engine."
    )


def _build_retrieval_report(
    ds: BenchmarkDataset, run: BenchmarkRun, configs: list[RetrievalConfig]
) -> RetrievalReport:
    """Turn a raw :class:`BenchmarkRun` into the fully-scored view model the report renders."""
    inscope = _in_scope_relevant(ds, run)
    n_scored = sum(1 for relevant in inscope.values() if relevant)
    n_permission_critical = len(ds.queries) - n_scored
    primary_key = _primary_config_key(configs)

    return RetrievalReport(
        primary_config_key=primary_key,
        ks=_KS,
        hit_ks=_HIT_KS,
        per_config=[_config_metrics(run, config, inscope) for config in configs],
        leakage=_leakage(run),
        weakest_queries=_weakest_queries(ds, run, inscope, primary_key),
        n_scored=n_scored,
        n_permission_critical=n_permission_critical,
        consistency_note=_consistency_note(ds, run),
    )


def _resolve_dataset_dir(name: str) -> str:
    """Resolve ``--dataset``: ``"golden"`` maps to the packaged dataset, else it is a path."""
    if name == "golden":
        return os.path.join(os.path.dirname(__file__), "golden")
    return name


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run_benchmark",
        description="Measure Third Brain retrieval quality, answer quality and performance.",
    )
    parser.add_argument(
        "--dataset",
        default="golden",
        help="'golden' (packaged) or a directory holding corpus.json + queries.json.",
    )
    parser.add_argument(
        "--configs",
        choices=["default", "sweep"],
        default="sweep",
        help="'sweep' = hybrid{on,off} x top_k{1,3,5,10}; 'default' = one hybrid_k8.",
    )
    parser.add_argument(
        "--answers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate RAG answers and score answer quality (default on).",
    )
    parser.add_argument(
        "--out",
        default="benchmarks/.report",
        help="Directory to write report.md + results.json into.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = _resolve_dataset_dir(args.dataset)

    logger.info(
        "benchmark_cli_start",
        dataset=args.dataset,
        configs=args.configs,
        answers=args.answers,
        out=args.out,
    )

    if not os.path.isdir(dataset_dir):
        print(f"Dataset directory not found: {dataset_dir}", file=sys.stderr)
        return 2

    ds = load_dataset(dataset_dir)
    problems = validate_dataset(ds)
    if problems:
        print("Dataset is invalid; aborting:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    configs = _build_configs(args.configs)
    run = asyncio.run(harness.run(ds, configs, with_answers=args.answers))

    retrieval_metrics = _build_retrieval_report(ds, run, configs)
    answer_scores = score_answers(ds, run)
    stage_latencies = measure_stage_latency(ds, configs)

    markdown = report.render(ds, run, retrieval_metrics, answer_scores, stage_latencies, args.out)
    print(markdown)

    leaks = retrieval_metrics.leakage.total_leaks
    logger.info(
        "benchmark_cli_done",
        provider_mode=run.provider_mode,
        primary_config=retrieval_metrics.primary_config_key,
        leaks=leaks,
        out=args.out,
    )
    if leaks > 0:
        print(
            f"FAIL: {leaks} permission leak(s) detected - see the permission section above.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
