"""Headless load-test runner with pass/fail thresholds.

Runs the Locust scenarios in ``loadtests/locustfile.py`` as a subprocess, streams its
output through, then parses the CSV stats it wrote and enforces the thresholds below.
Exits 0 when every threshold holds and 1 otherwise, so CI can gate on it. This module
never imports locust; it only needs locust installed in the interpreter's environment
(``pip install -e .[load]``).

Environment (defaults in parentheses):
* ``LOAD_USERS`` (50), ``LOAD_SPAWN_RATE`` (10), ``LOAD_DURATION`` (3m)
* ``LOAD_MAX_FAIL_PCT`` (1.0) - max overall failed-request percentage
* ``LOAD_P95_SEARCH_MS`` (750) - p95 budget for the /api/v1/search row
* ``LOAD_P95_CHAT_MS`` (1500) - p95 budget for the /api/v1/search/chat row
* ``LOAD_CSV_DIR`` (/tmp/tb_load) - where locust writes its CSV stats
* ``API_BASE`` (http://localhost:8000) - target host, also passed to the locustfile
* ``MANIFEST`` - inherited by the locustfile subprocess (seeder manifest path)

Usage: ``cd apps/api && python -m loadtests.run_load_test`` (or ``make load-test``).
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

SEARCH_NAME = "/api/v1/search"
CHAT_NAME = "/api/v1/search/chat"
AGGREGATED_NAME = "Aggregated"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _run_locust(csv_prefix: Path, api_base: str) -> int:
    """Run locust headless as a subprocess, inheriting stdout/stderr.

    ``--exit-code-on-error 0`` keeps locust's exit code about operational health only;
    failed requests are judged here against LOAD_MAX_FAIL_PCT instead.
    """
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(Path(__file__).with_name("locustfile.py")),
        "--headless",
        "-u",
        _env("LOAD_USERS", "50"),
        "-r",
        _env("LOAD_SPAWN_RATE", "10"),
        "--run-time",
        _env("LOAD_DURATION", "3m"),
        "--csv",
        str(csv_prefix),
        "--host",
        api_base,
        "--exit-code-on-error",
        "0",
    ]
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def _read_stats(stats_csv: Path) -> list[dict[str, str]]:
    with stats_csv.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _row_named(rows: list[dict[str, str]], name: str) -> dict[str, str] | None:
    for row in rows:
        if row.get("Name") == name:
            return row
    return None


def _num(row: dict[str, str] | None, column: str) -> float | None:
    """Parse a numeric CSV cell; locust writes ``N/A`` where it has no samples."""
    try:
        return float((row or {}).get(column, ""))
    except (TypeError, ValueError):
        return None


def _cell(row: dict[str, str], column: str, decimals: int = 0) -> str:
    value = _num(row, column)
    if value is None:
        return "-"
    return f"{value:.{decimals}f}"


def _print_table(rows: list[dict[str, str]]) -> None:
    """Print a compact per-endpoint summary (times in ms)."""
    header = (
        f"{'endpoint':<34} {'requests':>9} {'fails':>7} "
        f"{'median':>8} {'p95':>8} {'p99':>8} {'rps':>8}"
    )
    print()
    print(header)
    print("-" * len(header))
    ordered = [r for r in rows if r.get("Name") != AGGREGATED_NAME]
    aggregated = _row_named(rows, AGGREGATED_NAME)
    if aggregated is not None:
        ordered.append(aggregated)
    for row in ordered:
        print(
            f"{row.get('Name', '?'):<34} "
            f"{_cell(row, 'Request Count'):>9} "
            f"{_cell(row, 'Failure Count'):>7} "
            f"{_cell(row, 'Median Response Time'):>8} "
            f"{_cell(row, '95%'):>8} "
            f"{_cell(row, '99%'):>8} "
            f"{_cell(row, 'Requests/s', decimals=1):>8}"
        )
    print()


def _p95_check(rows: list[dict[str, str]], name: str, budget_ms: float) -> tuple[str, bool]:
    row = _row_named(rows, name)
    p95 = _num(row, "95%")
    if p95 is None:
        return f"{name} p95: no samples (budget {budget_ms:.0f}ms)", False
    return f"{name} p95 {p95:.0f}ms <= {budget_ms:.0f}ms", p95 <= budget_ms


def _evaluate(rows: list[dict[str, str]]) -> list[tuple[str, bool]]:
    """Build the (description, passed) verdicts for every configured threshold."""
    max_fail_pct = float(_env("LOAD_MAX_FAIL_PCT", "1.0"))
    p95_search_ms = float(_env("LOAD_P95_SEARCH_MS", "750"))
    p95_chat_ms = float(_env("LOAD_P95_CHAT_MS", "1500"))

    checks: list[tuple[str, bool]] = []
    aggregated = _row_named(rows, AGGREGATED_NAME)
    requests = _num(aggregated, "Request Count") or 0.0
    failures = _num(aggregated, "Failure Count") or 0.0
    if requests <= 0:
        checks.append(("overall: no requests were made", False))
    else:
        fail_pct = failures / requests * 100.0
        checks.append(
            (f"overall failure rate {fail_pct:.2f}% <= {max_fail_pct}%", fail_pct <= max_fail_pct)
        )
    checks.append(_p95_check(rows, SEARCH_NAME, p95_search_ms))
    checks.append(_p95_check(rows, CHAT_NAME, p95_chat_ms))
    return checks


def main() -> int:
    csv_dir = Path(_env("LOAD_CSV_DIR", "/tmp/tb_load"))
    csv_dir.mkdir(parents=True, exist_ok=True)
    api_base = _env("API_BASE", "http://localhost:8000")

    csv_prefix = csv_dir / "stats"
    returncode = _run_locust(csv_prefix, api_base)

    stats_csv = csv_dir / "stats_stats.csv"
    if not stats_csv.exists():
        print(f"locust wrote no stats CSV at {stats_csv} (exit code {returncode})")
        return returncode or 1

    rows = _read_stats(stats_csv)
    _print_table(rows)

    checks = _evaluate(rows)
    if returncode != 0:
        checks.append((f"locust exited cleanly (exit code {returncode})", False))
    failed = False
    for description, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {description}")
        failed = failed or not passed
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
