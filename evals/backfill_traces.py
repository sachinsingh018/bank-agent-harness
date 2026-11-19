"""Store the steps of runs recorded before run_traces existed. No model calls.

Usage (from the repo root):
    uv run python -m evals.backfill_traces [--runs evals/runs] [--allow-remote]

For every run directory under --runs, reads manifest.json and results.jsonl (each line is
validated as a CaseResult), finds the agent runs recorded for that eval run and writes one
run_traces row per case that has none yet, through harness.records.insert_trace_row. A case is
matched by its case id and must have the same status, reason and findings as the recorded run;
anything else is skipped and counted. One transaction per run directory. Rows are only added,
never changed. No case event is written (the audit log has no event type for it), so these
traces carry no trace_digest; existing run digests are untouched.

The write target comes from HARNESS_WRITE_DATABASE_URL only (never DATABASE_URL) and is never
printed; a remote target is refused unless --allow-remote is passed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, select

from evals.record_run import read_run_dir
from evals.run_aml_agent import (
    MANIFEST_FILE,
    RESULTS_FILE,
    SetupError,
    check_target,
    target_text,
)
from harness.db.records_models import AgentRun, RunTraceRow
from harness.db.session import DatabaseConnectionError, run_safely
from harness.records import (
    WRITE_URL_ENV,
    canonical_json,
    get_write_engine,
    insert_trace_row,
    trace_row,
)
from harness.trace import CaseResult

__all__ = ["BackfillCounts", "backfill_dir", "main", "run_dirs"]

DEFAULT_RUNS = Path("evals/runs")


@dataclass
class BackfillCounts:
    traced: int = 0
    already: int = 0
    not_recorded: int = 0
    mismatched: int = 0
    ambiguous: int = 0

    def text(self) -> str:
        return (
            f"traced {self.traced}, already stored {self.already}, not recorded "
            f"{self.not_recorded}, mismatched {self.mismatched}, ambiguous {self.ambiguous}"
        )


def run_dirs(root: Path) -> list[Path]:
    """Run directories with both run files, in name order (oldest first)."""
    if not root.is_dir():
        raise SetupError(f"{root} is not a directory")
    return sorted(
        d for d in root.iterdir() if (d / MANIFEST_FILE).is_file() and (d / RESULTS_FILE).is_file()
    )


def _same(result: CaseResult, run: Any) -> bool:
    if result.status != run.status or result.reason != run.reason:
        return False
    if result.findings is None or run.findings is None:
        return result.findings is None and run.findings is None
    return canonical_json(result.findings) == canonical_json(run.findings)


def backfill_dir(conn: Connection, eval_run_id: str, results: list[CaseResult]) -> BackfillCounts:
    """Add the missing run_traces rows of one eval run. The caller owns the transaction."""
    counts = BackfillCounts()
    runs = conn.execute(
        select(
            AgentRun.run_id,
            AgentRun.case_id,
            AgentRun.status,
            AgentRun.reason,
            AgentRun.findings,
        ).where(AgentRun.eval_run_id == eval_run_id)
    ).all()
    by_case: dict[str, list[Any]] = {}
    for run in runs:
        by_case.setdefault(run.case_id, []).append(run)
    run_ids = [r.run_id for r in runs]
    traced = set(
        conn.execute(select(RunTraceRow.run_id).where(RunTraceRow.run_id.in_(run_ids))).scalars()
    )
    seen_cases = Counter(r.case_id for r in results)
    for result in results:
        matches = by_case.get(result.case_id, [])
        if not matches:
            counts.not_recorded += 1
            continue
        if len(matches) > 1 or seen_cases[result.case_id] > 1:
            counts.ambiguous += 1
            continue
        run = matches[0]
        if run.run_id in traced:
            counts.already += 1
            continue
        if not _same(result, run):
            counts.mismatched += 1
            continue
        insert_trace_row(conn, trace_row(result, run.run_id))
        traced.add(run.run_id)
        counts.traced += 1
    return counts


def _work(
    engine: Engine, eval_run_id: str, results: list[CaseResult]
) -> Callable[[], BackfillCounts]:
    def work() -> BackfillCounts:
        with engine.begin() as conn:
            return backfill_dir(conn, eval_run_id, results)

    return work


def backfill(root: Path, allow_remote: bool) -> BackfillCounts:
    total = BackfillCounts()
    engine = get_write_engine(allow_remote=allow_remote)
    try:
        for run_dir in run_dirs(root):
            files = read_run_dir(run_dir, with_score=False)
            eval_run_id = str(files.manifest["run_id"])
            counts = run_safely(_work(engine, eval_run_id, files.results))
            print(f"{eval_run_id}: {counts.text()}")
            for field in ("traced", "already", "not_recorded", "mismatched", "ambiguous"):
                setattr(total, field, getattr(total, field) + getattr(counts, field))
    finally:
        engine.dispose()
    return total


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.backfill_traces")
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS, help="folder of run directories")
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow a database that is not local"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    write_url = os.environ.get(WRITE_URL_ENV)
    try:
        if not write_url:
            raise SetupError(f"{WRITE_URL_ENV} is not set")
        print("write " + target_text(write_url))
        check_target(write_url, args.allow_remote, WRITE_URL_ENV)
        total = backfill(args.runs, args.allow_remote)
    except (SetupError, DatabaseConnectionError, RuntimeError, KeyError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"done: {total.text()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
