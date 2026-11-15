"""Build the AML agent eval datasets (ids only) from the v1 alerts.

Usage (from the repo root):
    uv run python -m evals.make_aml_datasets --ruleset v1 [--check]

This file is one of the few allowed label readers (see evals/test_label_readers.py and
docs/decisions/0005). It is strictly read-only (SELECT only). The database URL comes from the
environment only and is never printed. The written files hold alert ids only, never labels or
counts of positives. Counts go to stdout only.

Assignment rules (deterministic):
- Each alert's laundering transfers that belong to a scheme are counted by the scheme split of
  evals/aml_rules_score.py (SPLIT_SEED 42). More development transfers: "dev". More held-out
  transfers: "heldout". A tie goes to "heldout" (conservative: never tune on a doubtful alert).
- Alerts with no laundering transfer in any scheme: the first hex digit of
  sha256("42:<alert_id>") decides. Even: "dev", odd: "heldout".
- Smoke set: 10 dev alerts. Dev alerts are ordered by sha256(alert_id). The first
  min(3, dev strict positives) strict positives in that order are taken, then the set is filled
  with the remaining dev alerts in the same order.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, select

from data.load_aml_slice import target_label
from evals.aml_rules_score import (
    SPLIT_SEED,
    Label,
    SchemeSplit,
    ScoredAlert,
    build_schemes,
    classify_alert,
    load_alerts_and_labels,
    split_schemes,
)
from harness.db.models import Alert
from harness.db.session import DatabaseConnectionError, get_database_url, get_engine, run_safely

DATASET_DIR = Path(__file__).parent / "datasets"
DATASET_VERSION = "v1"
CREATED_BY = "evals/make_aml_datasets.py"
SMOKE_SIZE = 10
SMOKE_MIN_STRICT = 3
MIN_STRICT_PER_SET = 15
LATE_CUTOFF = date(2022, 9, 10)  # decision 0006
SET_NAMES = ("dev", "heldout", "smoke")

Side = Literal["dev", "heldout"]


@dataclass(frozen=True)
class Datasets:
    dev: tuple[str, ...]
    heldout: tuple[str, ...]
    smoke: tuple[str, ...]

    def by_name(self) -> dict[str, tuple[str, ...]]:
        return {"dev": self.dev, "heldout": self.heldout, "smoke": self.smoke}


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def parity_side(alert_id: str) -> Side:
    """Side for an alert with no scheme transfers: parity of the first hex digit."""
    return "dev" if int(_hex(f"{SPLIT_SEED}:{alert_id}")[0], 16) % 2 == 0 else "heldout"


def assign_alert(alert: ScoredAlert, labels: Mapping[int, Label], split: SchemeSplit) -> Side:
    dev = held = 0
    for txn_id in alert.txn_ids:
        lab = labels.get(txn_id)
        if lab is None or not lab.is_laundering:
            continue
        if lab.attempt_id is None or lab.attempt_type is None:
            continue
        scheme = (lab.attempt_type, lab.attempt_id)
        if scheme in split.development:
            dev += 1
        elif scheme in split.held_out:
            held += 1
    if dev == 0 and held == 0:
        return parity_side(alert.alert_id)
    return "dev" if dev > held else "heldout"


def pick_smoke(dev_ids: Sequence[str], strict_ids: frozenset[str]) -> tuple[str, ...]:
    ordered = sorted(dev_ids, key=lambda a: (_hex(a), a))
    need = min(SMOKE_MIN_STRICT, sum(1 for a in ordered if a in strict_ids))
    picks = [a for a in ordered if a in strict_ids][:need]
    for alert_id in ordered:
        if len(picks) >= SMOKE_SIZE:
            break
        if alert_id not in picks:
            picks.append(alert_id)
    return tuple(sorted(picks))


def build_datasets(alerts: Sequence[ScoredAlert], labels: Mapping[int, Label]) -> Datasets:
    split = split_schemes(build_schemes(labels))
    dev: list[str] = []
    held: list[str] = []
    for alert in alerts:
        (dev if assign_alert(alert, labels, split) == "dev" else held).append(alert.alert_id)
    strict = frozenset(a.alert_id for a in alerts if classify_alert(a, labels)[0])
    return Datasets(tuple(sorted(dev)), tuple(sorted(held)), pick_smoke(dev, strict))


def render_dataset(name: str, alert_ids: Sequence[str], ruleset_version: str) -> str:
    body: dict[str, Any] = {
        "dataset": name,
        "version": DATASET_VERSION,
        "ruleset_version": ruleset_version,
        "alert_ids": sorted(alert_ids),
        "created_by": CREATED_BY,
        "split_seed": SPLIT_SEED,
    }
    return json.dumps(body, sort_keys=True, indent=2) + "\n"


def dataset_path(name: str, out_dir: Path = DATASET_DIR) -> Path:
    return out_dir / f"aml_alerts_{name}_{DATASET_VERSION}.json"


def render_all(datasets: Datasets, ruleset_version: str) -> dict[str, str]:
    return {
        name: render_dataset(name, ids, ruleset_version) for name, ids in datasets.by_name().items()
    }


def summarise(
    datasets: Datasets,
    alerts: Sequence[ScoredAlert],
    labels: Mapping[int, Label],
    window_ends: Mapping[str, datetime],
) -> list[str]:
    by_id = {a.alert_id: a for a in alerts}
    lines = ["set: alerts, strict positives, loose positives, late window (ends after 2022-09-10)"]
    warnings: list[str] = []
    for name, ids in datasets.by_name().items():
        flags = [classify_alert(by_id[a], labels) for a in ids]
        strict = sum(1 for s, _ in flags if s)
        loose = sum(1 for _, lo in flags if lo)
        late = sum(1 for a in ids if window_ends[a].date() > LATE_CUTOFF)
        lines.append(f"  {name}: {len(ids)}, {strict}, {loose}, {late}")
        if name != "smoke" and strict < MIN_STRICT_PER_SET:
            warnings.append(
                f"warning: {name} has {strict} strict positives, fewer than {MIN_STRICT_PER_SET}"
            )
    return lines + warnings


# ---------------------------------------------------------------------------------------------
# Database part. SELECT statements only.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Loaded:
    alerts: list[ScoredAlert]
    labels: dict[int, Label]
    window_ends: dict[str, datetime]


def load_from_connection(conn: Connection, ruleset_version: str) -> Loaded:
    alerts, labels = load_alerts_and_labels(conn, ruleset_version)
    rows = conn.execute(
        select(Alert.alert_id, Alert.window_end).where(Alert.ruleset_version == ruleset_version)
    ).all()
    return Loaded(alerts, labels, {alert_id: end for alert_id, end in rows})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.make_aml_datasets")
    parser.add_argument("--ruleset", required=True, help="ruleset version, for example v1")
    parser.add_argument(
        "--check", action="store_true", help="exit non-zero if the committed files differ"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        print(f"target: {target_label(get_database_url())}")
        engine = get_engine()
        try:

            def work() -> Loaded:
                with engine.connect() as conn:
                    return load_from_connection(conn, args.ruleset)

            loaded = run_safely(work)
        finally:
            engine.dispose()
    except (DatabaseConnectionError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    datasets = build_datasets(loaded.alerts, loaded.labels)
    print("\n".join(summarise(datasets, loaded.alerts, loaded.labels, loaded.window_ends)))
    rendered = render_all(datasets, args.ruleset)
    if args.check:
        differ = [
            name
            for name, text in rendered.items()
            if not dataset_path(name).exists()
            or dataset_path(name).read_text(encoding="utf-8") != text
        ]
        if differ:
            print(f"check failed, differs: {', '.join(differ)}")
            return 1
        print("check passed: committed datasets match")
        return 0
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        dataset_path(name).write_text(text, encoding="utf-8")
        print(f"dataset written: {dataset_path(name).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
