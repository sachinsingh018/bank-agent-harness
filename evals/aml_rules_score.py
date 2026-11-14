"""Score the AML rules against the answer key.

Usage (from the repo root):
    uv run python -m evals.aml_rules_score --ruleset v1 [--out PATH] [--no-write]

This is the only non-test code that reads the labels table. It is strictly read-only (SELECT
only). The database URL comes from the environment only and is never printed. The report holds
counts only.

The first half of the file is pure (no database code). The database part is at the bottom.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, select

from data.load_aml_slice import target_label
from harness.db.models import Alert, AlertTransaction, TransactionLabel
from harness.db.session import DatabaseConnectionError, get_database_url, get_engine, run_safely

SPLIT_SEED = 42
DEV_PER_FAMILY = 4
DEFAULT_OUT_DIR = Path(__file__).parent / "baselines"

Scheme = tuple[str, int]  # (family, attempt_id)


@dataclass(frozen=True)
class ScoredAlert:
    alert_id: str
    detector: str
    txn_ids: frozenset[int]


@dataclass(frozen=True)
class Label:
    is_laundering: bool
    attempt_id: int | None
    attempt_type: str | None


@dataclass(frozen=True)
class SchemeSplit:
    development: frozenset[Scheme]
    held_out: frozenset[Scheme]

    def counts(self) -> dict[str, int]:
        return {"development": len(self.development), "held_out": len(self.held_out)}


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 6)


def build_schemes(labels: Mapping[int, Label]) -> dict[Scheme, frozenset[int]]:
    """Laundering transfers grouped by scheme."""
    groups: dict[Scheme, set[int]] = defaultdict(set)
    for txn_id, lab in labels.items():
        if lab.is_laundering and lab.attempt_id is not None and lab.attempt_type is not None:
            groups[(lab.attempt_type, lab.attempt_id)].add(txn_id)
    return {k: frozenset(v) for k, v in groups.items()}


def _split_key(family: str, attempt_id: int) -> str:
    return hashlib.sha256(f"{SPLIT_SEED}:{family}:{attempt_id}".encode()).hexdigest()


def split_schemes(schemes: Iterable[Scheme]) -> SchemeSplit:
    """Per family, the first 4 by sha256 order are development, the rest held out."""
    by_family: dict[str, list[int]] = defaultdict(list)
    for family, attempt_id in set(schemes):
        by_family[family].append(attempt_id)
    dev: set[Scheme] = set()
    held: set[Scheme] = set()
    for family, ids in by_family.items():
        ordered = sorted(ids, key=lambda a: (_split_key(family, a), a))
        dev.update((family, a) for a in ordered[:DEV_PER_FAMILY])
        held.update((family, a) for a in ordered[DEV_PER_FAMILY:])
    return SchemeSplit(frozenset(dev), frozenset(held))


def classify_alert(alert: ScoredAlert, labels: Mapping[int, Label]) -> tuple[bool, bool]:
    """Return (true_strict, true_loose). Strict: at least half laundering. Loose: at least one."""
    n = len(alert.txn_ids)
    laundering = sum(1 for t in alert.txn_ids if t in labels and labels[t].is_laundering)
    return (n > 0 and 2 * laundering >= n), laundering >= 1


def _alert_family(alert: ScoredAlert, labels: Mapping[int, Label]) -> str | None:
    """Family holding most of the alert's laundering transfers (ties: alphabetical)."""
    counts: Counter[str] = Counter()
    for t in alert.txn_ids:
        lab = labels.get(t)
        if lab is not None and lab.is_laundering and lab.attempt_type:
            counts[lab.attempt_type] += 1
    if not counts:
        return None
    best = max(counts.values())
    return sorted(f for f, c in counts.items() if c == best)[0]


def _scope_schemes(
    schemes: Mapping[Scheme, frozenset[int]], split: SchemeSplit
) -> dict[str, dict[Scheme, frozenset[int]]]:
    detectable = {k: v for k, v in schemes.items() if len(v) >= 2}
    return {
        "all": dict(schemes),
        "detectable": detectable,
        "development": {k: v for k, v in schemes.items() if k in split.development},
        "held_out": {k: v for k, v in schemes.items() if k in split.held_out},
        "held_out_detectable": {k: v for k, v in detectable.items() if k in split.held_out},
    }


def recall_metrics(
    alerts: Sequence[ScoredAlert], schemes: Mapping[Scheme, frozenset[int]]
) -> dict[str, Any]:
    """Recall over the given schemes using the given alerts."""
    in_alert: set[int] = set()
    by_txn: dict[int, list[int]] = defaultdict(list)
    for i, a in enumerate(alerts):
        in_alert |= a.txn_ids
        for t in a.txn_ids:
            by_txn[t].append(i)
    caught = touched = 0
    laundering = covered = 0
    for txns in schemes.values():
        per_alert: Counter[int] = Counter()
        for t in txns:
            for i in by_txn.get(t, ()):
                per_alert[i] += 1
        if per_alert:
            touched += 1
        if any(2 * c >= len(txns) for c in per_alert.values()):
            caught += 1
        laundering += len(txns)
        covered += len(txns & in_alert)
    total = len(schemes)
    return {
        "schemes": total,
        "caught": caught,
        "touched": touched,
        "scheme_recall_caught": _ratio(caught, total),
        "scheme_recall_touched": _ratio(touched, total),
        "laundering_transfers": laundering,
        "laundering_transfers_in_alerts": covered,
        "transfer_recall": _ratio(covered, laundering),
    }


def _recall_all_scopes(
    alerts: Sequence[ScoredAlert],
    schemes: Mapping[Scheme, frozenset[int]],
    split: SchemeSplit,
) -> dict[str, Any]:
    return {
        scope: recall_metrics(alerts, subset)
        for scope, subset in _scope_schemes(schemes, split).items()
    }


def _alert_metrics(alerts: Sequence[ScoredAlert], labels: Mapping[int, Label]) -> dict[str, Any]:
    flags = [classify_alert(a, labels) for a in alerts]
    raised = len(alerts)
    true_strict = sum(1 for s, _ in flags if s)
    true_loose = sum(1 for _, lo in flags if lo)
    return {
        "raised": raised,
        "true_strict": true_strict,
        "true_loose": true_loose,
        "false_strict": raised - true_strict,
        "false_loose": raised - true_loose,
        "precision_strict": _ratio(true_strict, raised),
        "precision_loose": _ratio(true_loose, raised),
    }


def _block(
    alerts: Sequence[ScoredAlert],
    labels: Mapping[int, Label],
    schemes: Mapping[Scheme, frozenset[int]],
    split: SchemeSplit,
) -> dict[str, Any]:
    """Overall and per-detector block. Alert counts and recall both use the given alerts."""
    alert_part = _alert_metrics(alerts, labels)
    recall = _recall_all_scopes(alerts, schemes, split)
    return {
        "alerts": alert_part,
        "false_alerts_per_caught_scheme": _ratio(
            alert_part["false_strict"], recall["all"]["caught"]
        ),
        "recall": recall,
    }


def _family_block(
    attributed: Sequence[ScoredAlert],
    all_alerts: Sequence[ScoredAlert],
    labels: Mapping[int, Label],
    schemes: Mapping[Scheme, frozenset[int]],
    split: SchemeSplit,
) -> dict[str, Any]:
    """Per-family block. Attributed counts only. Recall uses all alerts."""
    flags = [classify_alert(a, labels) for a in attributed]
    return {
        "attributed_alerts": {
            "raised": len(attributed),
            "true_strict": sum(1 for s, _ in flags if s),
            "true_loose": sum(1 for _, lo in flags if lo),
        },
        "recall": _recall_all_scopes(all_alerts, schemes, split),
    }


def score(
    alerts: Sequence[ScoredAlert], labels: Mapping[int, Label]
) -> tuple[dict[str, Any], SchemeSplit, dict[Scheme, frozenset[int]]]:
    schemes = build_schemes(labels)
    split = split_schemes(schemes)
    families = sorted({f for f, _ in schemes})
    detectors = sorted({a.detector for a in alerts})
    metrics: dict[str, Any] = {
        "overall": _block(alerts, labels, schemes, split),
        "per_detector": {
            d: (lambda sub: _block(sub, labels, schemes, split))(
                [a for a in alerts if a.detector == d]
            )
            for d in detectors
        },
        "per_family": {
            f: _family_block(
                [a for a in alerts if _alert_family(a, labels) == f],
                alerts,
                labels,
                {k: v for k, v in schemes.items() if k[0] == f},
                split,
            )
            for f in families
        },
    }
    return metrics, split, schemes


NOTES: tuple[str, ...] = (
    "v1 used starting values and was not tuned.",
    "The numbers apply to this slice only (17 days, sampled normal traffic).",
    "Recall is reported on all, detectable (2 or more transfers), development and held-out "
    "schemes. The held-out numbers are the headline.",
    "Precision uses all alerts and is not split by scheme.",
    "Structuring and volume spike are not part of v1.",
    "Per-family alert counts include only alerts that contain at least one laundering "
    "transfer, attributed to the family holding most of those transfers (ties alphabetical).",
    "Alerts with no laundering transfer appear in no family, so per-family numbers do not "
    "describe false alarms. Use the overall and per-detector figures for precision and noise.",
    "Per-detector recall uses only that detector's alerts. Per-family recall uses all alerts.",
)


def build_report(
    ruleset_version: str, alerts: Sequence[ScoredAlert], labels: Mapping[int, Label]
) -> dict[str, Any]:
    metrics, split, schemes = score(alerts, labels)
    detectable = sum(1 for v in schemes.values() if len(v) >= 2)
    digest = hashlib.sha256("\n".join(sorted(a.alert_id for a in alerts)).encode()).hexdigest()
    return {
        "ruleset_version": ruleset_version,
        "dataset": {
            "transactions": len(labels),
            "laundering_transfers": sum(len(v) for v in schemes.values()),
            "schemes": len(schemes),
            "detectable_schemes": detectable,
        },
        "split": {
            "seed": SPLIT_SEED,
            "development_per_family": DEV_PER_FAMILY,
            **split.counts(),
        },
        "alert_counts": dict(sorted(Counter(a.detector for a in alerts).items())),
        "alert_total": len(alerts),
        "alert_set_hash": digest,
        "metrics": metrics,
        "notes": list(NOTES),
    }


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=2) + "\n"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_summary(report: Mapping[str, Any]) -> str:
    m = report["metrics"]
    overall = m["overall"]
    lines = [f"ruleset: {report['ruleset_version']}", f"alerts: {report['alert_total']}"]
    lines.append("headline (held-out schemes only):")
    for scope in ("held_out", "held_out_detectable"):
        r = overall["recall"][scope]
        lines.append(
            f"  {scope}: schemes {r['schemes']}, caught {r['caught']} "
            f"({_fmt(r['scheme_recall_caught'])}), touched {r['touched']} "
            f"({_fmt(r['scheme_recall_touched'])}), transfer recall {_fmt(r['transfer_recall'])}"
        )
    a = overall["alerts"]
    lines.append(
        f"precision strict (all alerts): {_fmt(a['precision_strict'])} "
        f"(loose {_fmt(a['precision_loose'])})"
    )
    lines.append(
        "false alerts per caught scheme (all schemes): "
        f"{_fmt(overall['false_alerts_per_caught_scheme'])}"
    )
    lines.append("per detector: alerts, true strict, precision strict, caught (all schemes)")
    for det, blk in m["per_detector"].items():
        al = blk["alerts"]
        lines.append(
            f"  {det}: {al['raised']}, {al['true_strict']}, {_fmt(al['precision_strict'])}, "
            f"{blk['recall']['all']['caught']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Database part. SELECT statements only.
# ---------------------------------------------------------------------------------------------


def score_from_connection(conn: Connection, ruleset_version: str) -> dict[str, Any]:
    alert_rows = conn.execute(
        select(Alert.alert_id, Alert.detector).where(Alert.ruleset_version == ruleset_version)
    ).all()
    if not alert_rows:
        raise ValueError("no alerts found for this ruleset version, run the rules first")
    link_rows = conn.execute(
        select(AlertTransaction.alert_id, AlertTransaction.txn_id)
        .join(Alert, Alert.alert_id == AlertTransaction.alert_id)
        .where(Alert.ruleset_version == ruleset_version)
    ).all()
    links: dict[str, set[int]] = defaultdict(set)
    for alert_id, txn_id in link_rows:
        links[alert_id].add(txn_id)
    alerts = [
        ScoredAlert(alert_id, detector, frozenset(links.get(alert_id, ())))
        for alert_id, detector in alert_rows
    ]
    label_rows = conn.execute(
        select(
            TransactionLabel.txn_id,
            TransactionLabel.is_laundering,
            TransactionLabel.attempt_id,
            TransactionLabel.attempt_type,
        )
    ).all()
    labels = {t: Label(bool(lau), aid, atype) for t, lau, aid, atype in label_rows}
    return build_report(ruleset_version, alerts, labels)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.aml_rules_score")
    parser.add_argument("--ruleset", required=True, help="ruleset version, for example v1")
    parser.add_argument("--out", type=Path, default=None, help="report path")
    parser.add_argument("--no-write", action="store_true", help="print only, write no file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out: Path = args.out or DEFAULT_OUT_DIR / f"aml_rules_{args.ruleset}.json"
    try:
        print(f"target: {target_label(get_database_url())}")
        engine = get_engine()
        try:

            def work() -> dict[str, Any]:
                with engine.connect() as conn:
                    return score_from_connection(conn, args.ruleset)

            report = run_safely(work)
        finally:
            engine.dispose()
    except (DatabaseConnectionError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(render_summary(report))
    if args.no_write:
        print("no file written")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_json(report), encoding="utf-8")
        print(f"report written: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
