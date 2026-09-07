"""Render a benchmark run into a human-readable markdown report and a machine-readable
``results.json``.

The report ties together everything the harness and scorers produced: retrieval quality
(per config and for a featured "primary" config), the permission-correctness leakage check
(a hard pass/fail), answer quality, single-request performance, and a drill-down of the
weakest queries so a reader learns not just the aggregate numbers but *where* retrieval
fails. All numbers are computed upstream by :mod:`benchmarks.run_benchmark`; this module is
purely presentational and pulls in no infrastructure (only :mod:`json` and :mod:`os`), so it
imports and runs offline.

The view models below (:class:`ConfigMetrics`, :class:`QueryDiagnostic`,
:class:`LeakageReport`, :class:`RetrievalReport`) are the contract between the CLI (which
computes them with :mod:`benchmarks.metrics`) and this renderer. Keeping them here lets the
renderer own the shape it consumes without importing the CLI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchmarks.dataset import BenchmarkDataset
    from benchmarks.harness import BenchmarkRun
    from benchmarks.judge import AnswerScores
    from benchmarks.perf import StageLatency


@dataclass
class ConfigMetrics:
    """Averaged retrieval-quality metrics for one retrieval config.

    ``recall_at`` / ``precision_at`` are keyed by k in :data:`RetrievalReport.ks`,
    ``hit_rate_at`` by k in :data:`RetrievalReport.hit_ks`. ``median_latency_ms`` is the
    median wall-clock retrieval latency (the ``retrieve`` call only) across every query,
    including the permission-critical ones excluded from the quality averages. ``n_scored``
    counts the queries that contributed to the quality averages (those with a non-empty
    in-scope relevant set).
    """

    config_key: str
    hybrid: bool
    top_k: int
    recall_at: dict[int, float]
    precision_at: dict[int, float]
    hit_rate_at: dict[int, float]
    mrr: float
    map: float
    ndcg_at_10: float
    median_latency_ms: float
    n_scored: int


@dataclass
class QueryDiagnostic:
    """One query's outcome under the primary config, for the weakest-query drill-down."""

    query_id: str
    config_key: str
    principal_key: str
    text: str
    in_scope_relevant: list[str]
    retrieved: list[str]
    recall_at_5: float


@dataclass
class LeakageReport:
    """The permission-correctness result: any ranked doc outside the caller's ground-truth
    visible set is a leak, and a single leak fails the whole run."""

    total_leaks: int
    total_ranked: int
    leak_rate: float
    leaks: list[dict] = field(default_factory=list)


@dataclass
class RetrievalReport:
    """Everything the renderer needs about retrieval quality and permission correctness.

    ``per_config`` holds one :class:`ConfigMetrics` per benchmarked config; ``primary_config_key``
    names the one featured in the headline quality table. ``n_permission_critical`` counts the
    queries whose entire relevant set is out of the asker's scope (excluded from quality
    averages, validated instead by the leakage check).
    """

    primary_config_key: str
    ks: list[int]
    hit_ks: list[int]
    per_config: list[ConfigMetrics]
    leakage: LeakageReport
    weakest_queries: list[QueryDiagnostic]
    n_scored: int
    n_permission_critical: int
    consistency_note: str


def _f3(value: float) -> str:
    return f"{value:.3f}"


def _f1(value: float) -> str:
    return f"{value:.1f}"


def _opt3(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _cell(value: object) -> str:
    """Sanitize a value for a markdown table cell (escape pipes, flatten newlines)."""
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _truncate(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _mean_opt(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _primary(metrics: RetrievalReport) -> ConfigMetrics:
    for cm in metrics.per_config:
        if cm.config_key == metrics.primary_config_key:
            return cm
    return metrics.per_config[0]


def _banner(dataset: BenchmarkDataset, run: BenchmarkRun, answers_ran: bool) -> list[str]:
    config_keys = ", ".join(c.key for c in run.configs)
    out = [
        "# Third Brain benchmark report",
        "",
        (
            f"Dataset: {dataset.name} | documents: {len(dataset.documents)} | "
            f"collections: {len(dataset.collections)} | principals: {len(dataset.principals)} | "
            f"queries: {len(dataset.queries)}"
        ),
        f"Provider mode: {run.provider_mode} | configs: {config_keys} | "
        f"answers: {'on' if answers_ran else 'off'}",
    ]
    if run.provider_mode == "offline":
        out.append(
            "Note: running offline against the deterministic stub embeddings. Semantic numbers "
            "are lexical/hybrid approximations and the LLM judge is skipped; retrieval and "
            "permission checks are still exercised end-to-end against real pgvector."
        )
    else:
        out.append(
            "Note: running with a live provider. Retrieval, answer, and LLM-judge numbers are real."
        )
    return out


def _retrieval_quality_section(metrics: RetrievalReport) -> list[str]:
    cm = _primary(metrics)
    rows: list[list[object]] = []
    for k in metrics.ks:
        rows.append([f"recall@{k}", _f3(cm.recall_at[k])])
    for k in metrics.ks:
        rows.append([f"precision@{k}", _f3(cm.precision_at[k])])
    rows.append(["MRR", _f3(cm.mrr)])
    rows.append(["MAP", _f3(cm.map)])
    rows.append(["nDCG@10", _f3(cm.ndcg_at_10)])
    for k in metrics.hit_ks:
        rows.append([f"hit-rate@{k}", _f3(cm.hit_rate_at[k])])
    return [
        "## Retrieval quality (primary config)",
        "",
        (
            f"Featured config: `{cm.config_key}` (hybrid={cm.hybrid}, top_k={cm.top_k}), averaged "
            f"over {cm.n_scored} scored quer{'y' if cm.n_scored == 1 else 'ies'}. "
            f"{metrics.n_permission_critical} permission-critical quer"
            f"{'y is' if metrics.n_permission_critical == 1 else 'ies are'} excluded from these "
            "averages (they have no in-scope relevant doc) and are validated by the leakage check "
            "instead."
        ),
        "",
        _table(["metric", "value"], rows),
    ]


def _config_comparison_section(metrics: RetrievalReport) -> list[str]:
    rows: list[list[object]] = []
    for cm in metrics.per_config:
        rows.append(
            [
                cm.config_key,
                cm.hybrid,
                cm.top_k,
                _f3(cm.recall_at[5]),
                _f3(cm.ndcg_at_10),
                _f3(cm.mrr),
                _f1(cm.median_latency_ms),
            ]
        )
    takeaways = _config_takeaways(metrics)
    return [
        "## Config comparison",
        "",
        _table(
            [
                "config",
                "hybrid",
                "top_k",
                "recall@5",
                "nDCG@10",
                "MRR",
                "median retrieval ms",
            ],
            rows,
        ),
        "",
        "Takeaway: " + takeaways,
    ]


def _config_takeaways(metrics: RetrievalReport) -> str:
    parts: list[str] = []
    hybrids = [cm for cm in metrics.per_config if cm.hybrid]
    vectors = [cm for cm in metrics.per_config if not cm.hybrid]
    if hybrids and vectors:
        hy = sum(cm.recall_at[5] for cm in hybrids) / len(hybrids)
        ve = sum(cm.recall_at[5] for cm in vectors) / len(vectors)
        parts.append(
            f"hybrid vs vector-only recall@5 is {hy:.3f} vs {ve:.3f} (delta {hy - ve:+.3f})"
        )

    cm = _primary(metrics)
    r_top = cm.recall_at[metrics.ks[-1]]
    sat_k = metrics.ks[-1]
    if r_top > 0:
        for k in metrics.ks:
            if cm.recall_at[k] >= 0.99 * r_top:
                sat_k = k
                break
        parts.append(f"on `{cm.config_key}` the recall@k curve saturates by k={sat_k}")
    else:
        parts.append(f"`{cm.config_key}` recalls no in-scope relevant docs (recall@k is 0)")
    return "; ".join(parts) + "."


def _permission_section(metrics: RetrievalReport) -> list[str]:
    leak = metrics.leakage
    verdict = "**PASS**" if leak.total_leaks == 0 else "**FAIL**"
    cm = _primary(metrics)
    out = [
        "## Permission correctness",
        "",
        (
            f"Leakage: {leak.leak_rate:.3f} ({leak.total_leaks} leaked doc"
            f"{'' if leak.total_leaks == 1 else 's'} across {leak.total_ranked} ranked results) "
            f"-> {verdict}"
        ),
        "",
        f"In-scope recall@5 (`{cm.config_key}`): {_f3(cm.recall_at[5])} - the caller still finds "
        "what they are allowed to see after permission filtering.",
        "",
        f"Cross-principal consistency: {metrics.consistency_note}",
    ]
    if leak.leaks:
        out += [
            "",
            "Leaked results (each is a hard failure):",
            "",
            _table(
                ["query", "config", "principal", "leaked doc"],
                [
                    [item["query_id"], item["config_key"], item["principal_key"], item["doc_id"]]
                    for item in leak.leaks[:20]
                ],
            ),
        ]
    return out


def _answer_quality_section(
    run: BenchmarkRun, answer_scores: list[AnswerScores], answers_ran: bool
) -> list[str]:
    if not answers_ran or not answer_scores:
        return [
            "## Answer quality",
            "",
            "Answers were not generated for this run (--no-answers); answer quality is skipped.",
        ]

    by_config: dict[str, list[AnswerScores]] = {}
    for score in answer_scores:
        by_config.setdefault(score.config_key, []).append(score)

    offline = run.provider_mode == "offline"

    def _llm_cell(values: list[float | None]) -> str:
        if offline:
            return "skipped"
        return _opt3(_mean_opt(values))

    rows: list[list[object]] = []
    for config in run.configs:
        scores = by_config.get(config.key)
        if not scores:
            continue
        cp = sum(s.citation_precision for s in scores) / len(scores)
        cr = sum(s.citation_recall for s in scores) / len(scores)
        kc = sum(s.keyword_coverage for s in scores) / len(scores)
        rows.append(
            [
                config.key,
                _f3(cp),
                _f3(cr),
                _f3(kc),
                _llm_cell([s.llm_faithful for s in scores]),
                _llm_cell([s.llm_correct for s in scores]),
            ]
        )

    note = (
        "LLM faithfulness/correctness are skipped offline (deterministic stub answers)."
        if offline
        else "LLM faithfulness/correctness are from the live judge; n/a means no basis to score."
    )
    return [
        "## Answer quality",
        "",
        _table(
            [
                "config",
                "citation precision",
                "citation recall",
                "keyword coverage",
                "llm faithful",
                "llm correct",
            ],
            rows,
        ),
        "",
        note,
    ]


_STAGE_COLUMNS = [
    ("retrieval.scope", "scope"),
    ("retrieval.embed_query", "embed"),
    ("retrieval.vector_search", "vector"),
    ("retrieval.keyword_search", "keyword"),
    ("retrieval.retrieve", "retrieve"),
    ("rag.answer", "answer"),
    ("total", "total"),
]


def _performance_section(
    metrics: RetrievalReport, stage_latencies: list[StageLatency]
) -> list[str]:
    if not stage_latencies:
        return ["## Performance", "", "No per-stage latency was captured for this run."]

    rows: list[list[object]] = []
    for sl in stage_latencies:
        row: list[object] = [sl.config_key]
        for key, _label in _STAGE_COLUMNS:
            value = sl.per_stage_ms.get(key)
            row.append(_f1(value) if value is not None else "-")
        rows.append(row)

    section = [
        "## Performance",
        "",
        "Per-stage median latency (milliseconds), from the OpenTelemetry spans the services "
        "already emit:",
        "",
        _table(["config"] + [label for _key, label in _STAGE_COLUMNS], rows),
    ]

    tradeoff = _perf_tradeoff(metrics, stage_latencies)
    if tradeoff:
        section += ["", tradeoff]
    return section


def _perf_tradeoff(metrics: RetrievalReport, stage_latencies: list[StageLatency]) -> str:
    totals = {sl.config_key: sl.per_stage_ms.get("total") for sl in stage_latencies}
    recall5 = {cm.config_key: cm.recall_at[5] for cm in metrics.per_config}
    timed = [key for key, total in totals.items() if total is not None and key in recall5]
    if not timed:
        return ""

    best = max(timed, key=lambda key: recall5[key])
    fastest = min(timed, key=lambda key: totals[key])
    return (
        f"Quality/latency tradeoff: best recall@5 is `{best}` ({recall5[best]:.3f}) at "
        f"{totals[best]:.1f} ms end-to-end; fastest end-to-end is `{fastest}` "
        f"({totals[fastest]:.1f} ms) at recall@5 {recall5[fastest]:.3f}."
    )


def _weakest_section(metrics: RetrievalReport) -> list[str]:
    if not metrics.weakest_queries:
        return ["## Weakest queries", "", "No scored queries to drill into."]
    rows = [
        [
            d.query_id,
            _truncate(d.text),
            ", ".join(d.in_scope_relevant) or "(none)",
            ", ".join(d.retrieved) or "(none)",
            _f3(d.recall_at_5),
        ]
        for d in metrics.weakest_queries
    ]
    return [
        "## Weakest queries",
        "",
        f"The lowest recall@5 queries under `{metrics.primary_config_key}` (in-scope relevant "
        "vs what was retrieved):",
        "",
        _table(
            ["query", "text", "in-scope relevant", "retrieved (top 5)", "recall@5"],
            rows,
        ),
    ]


def _results_dict(
    dataset: BenchmarkDataset,
    run: BenchmarkRun,
    metrics: RetrievalReport,
    answer_scores: list[AnswerScores],
    stage_latencies: list[StageLatency],
    answers_ran: bool,
) -> dict:
    def _config_dict(cm: ConfigMetrics) -> dict:
        entry: dict = {
            "hybrid": cm.hybrid,
            "top_k": cm.top_k,
            "mrr": cm.mrr,
            "map": cm.map,
            "ndcg_at_10": cm.ndcg_at_10,
            "median_latency_ms": cm.median_latency_ms,
            "n_scored": cm.n_scored,
        }
        for k in metrics.ks:
            entry[f"recall_at_{k}"] = cm.recall_at[k]
            entry[f"precision_at_{k}"] = cm.precision_at[k]
        for k in metrics.hit_ks:
            entry[f"hit_rate_at_{k}"] = cm.hit_rate_at[k]
        return entry

    answers: dict[str, dict] = {}
    if answers_ran and answer_scores:
        by_config: dict[str, list[AnswerScores]] = {}
        for score in answer_scores:
            by_config.setdefault(score.config_key, []).append(score)
        for config_key, scores in by_config.items():
            answers[config_key] = {
                "citation_precision": sum(s.citation_precision for s in scores) / len(scores),
                "citation_recall": sum(s.citation_recall for s in scores) / len(scores),
                "keyword_coverage": sum(s.keyword_coverage for s in scores) / len(scores),
                "llm_faithful": _mean_opt([s.llm_faithful for s in scores]),
                "llm_correct": _mean_opt([s.llm_correct for s in scores]),
            }

    return {
        "dataset": {
            "name": dataset.name,
            "documents": len(dataset.documents),
            "collections": len(dataset.collections),
            "principals": len(dataset.principals),
            "queries": len(dataset.queries),
        },
        "provider_mode": run.provider_mode,
        "answers": answers_ran,
        "primary_config": metrics.primary_config_key,
        "n_scored": metrics.n_scored,
        "n_permission_critical": metrics.n_permission_critical,
        "retrieval": {cm.config_key: _config_dict(cm) for cm in metrics.per_config},
        "permission": {
            "total_leaks": metrics.leakage.total_leaks,
            "total_ranked": metrics.leakage.total_ranked,
            "leak_rate": metrics.leakage.leak_rate,
            "pass": metrics.leakage.total_leaks == 0,
            "consistency_note": metrics.consistency_note,
            "leaks": metrics.leakage.leaks,
        },
        "answer_quality": answers,
        "performance": {sl.config_key: sl.per_stage_ms for sl in stage_latencies},
        "weakest_queries": [
            {
                "query_id": d.query_id,
                "principal_key": d.principal_key,
                "text": d.text,
                "in_scope_relevant": d.in_scope_relevant,
                "retrieved": d.retrieved,
                "recall_at_5": d.recall_at_5,
            }
            for d in metrics.weakest_queries
        ],
    }


def render(
    dataset: BenchmarkDataset,
    run: BenchmarkRun,
    retrieval_metrics: RetrievalReport,
    answer_scores: list[AnswerScores],
    stage_latencies: list[StageLatency],
    out_dir: str,
) -> str:
    """Render ``run`` into markdown, write ``report.md`` + ``results.json`` under ``out_dir``,
    and return the markdown text.

    The renderer performs no measurement of its own: ``retrieval_metrics`` (quality + leakage),
    ``answer_scores`` and ``stage_latencies`` are computed upstream and only formatted here.
    """
    answers_ran = any(qr.answer is not None for qr in run.runs)

    sections: list[list[str]] = [
        _banner(dataset, run, answers_ran),
        _retrieval_quality_section(retrieval_metrics),
        _config_comparison_section(retrieval_metrics),
        _permission_section(retrieval_metrics),
        _answer_quality_section(run, answer_scores, answers_ran),
        _performance_section(retrieval_metrics, stage_latencies),
        _weakest_section(retrieval_metrics),
    ]
    markdown = "\n\n".join("\n".join(section) for section in sections) + "\n"

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown)
    results = _results_dict(
        dataset, run, retrieval_metrics, answer_scores, stage_latencies, answers_ran
    )
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")

    return markdown
