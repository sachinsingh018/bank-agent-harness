"""Run the AML rules over the stored transactions and write alerts.

Usage (from the repo root):
    uv run python -m modules.aml.run_rules --ruleset v1 [--dry-run] [--replace]

The database URL comes from the environment only and is never printed. Only counts are shown.
The agent never runs this script. Alerts are derived data, written by a human operator.
"""

import argparse
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine

from data.load_aml_slice import target_label
from harness.db.session import DatabaseConnectionError, get_database_url, get_engine, run_safely
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import DETECTOR_ORDER, run_all_with_stats
from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.store import (
    delete_alerts_for_version_on,
    write_alerts,
    write_alerts_on,
)

RULESETS: dict[str, RulesetConfig] = {"v1": V1}


@dataclass(frozen=True)
class RunSummary:
    ruleset_version: str
    transfers: int
    total: int
    per_detector: dict[str, int] = field(default_factory=dict)
    capped_starts: int = 0
    dry_run: bool = False
    deleted: int = 0
    inserted_alerts: int = 0
    inserted_links: int = 0


def run(engine: Engine, ruleset_version: str, dry_run: bool, replace: bool) -> RunSummary:
    config = RULESETS.get(ruleset_version)
    if config is None:
        raise ValueError("unknown ruleset version")
    txns = load_txns(engine)
    outcome = run_all_with_stats(txns, config)
    drafts = outcome.drafts
    counts = Counter(d.detector for d in drafts)
    per_detector = {name: counts.get(name, 0) for name in DETECTOR_ORDER}
    base = RunSummary(
        ruleset_version=ruleset_version,
        transfers=len(txns),
        total=len(drafts),
        per_detector=per_detector,
        capped_starts=outcome.capped_starts,
        dry_run=dry_run,
    )
    if dry_run:
        return base
    if replace:

        def work() -> tuple[int, dict[str, int]]:
            with engine.begin() as conn:
                deleted = delete_alerts_for_version_on(conn, ruleset_version)
                return deleted, write_alerts_on(conn, drafts, ruleset_version)

        deleted, inserted = run_safely(work)
    else:
        deleted = 0
        inserted = write_alerts(engine, drafts, ruleset_version)
    return RunSummary(
        ruleset_version=base.ruleset_version,
        transfers=base.transfers,
        total=base.total,
        per_detector=base.per_detector,
        capped_starts=base.capped_starts,
        dry_run=False,
        deleted=deleted,
        inserted_alerts=inserted["alerts"],
        inserted_links=inserted["alert_transactions"],
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m modules.aml.run_rules")
    parser.add_argument("--ruleset", required=True, help="ruleset version, for example v1")
    parser.add_argument("--dry-run", action="store_true", help="read and count, never write")
    parser.add_argument(
        "--replace", action="store_true", help="rebuild only the alerts of this ruleset version"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.ruleset not in RULESETS:
        print("error: unknown ruleset version")
        return 1
    try:
        print(f"target: {target_label(get_database_url())}")
        engine = get_engine()
        try:
            started = time.monotonic()
            summary = run(engine, args.ruleset, args.dry_run, args.replace)
            elapsed = time.monotonic() - started
        finally:
            engine.dispose()
    except (DatabaseConnectionError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"transfers read: {summary.transfers}")
    print(f"alerts: {summary.total}")
    for name, count in summary.per_detector.items():
        print(f"  {name}: {count}")
    print(f"cycle search starts that hit the step cap: {summary.capped_starts}")
    if summary.dry_run:
        print("dry run: nothing written")
    else:
        if args.replace:
            print(f"deleted alerts of {summary.ruleset_version}: {summary.deleted}")
        print(f"inserted into alerts: {summary.inserted_alerts}")
        print(f"inserted into alert_transactions: {summary.inserted_links}")
    print(f"time: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
