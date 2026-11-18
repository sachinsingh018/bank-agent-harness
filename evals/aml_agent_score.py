"""Score an AML agent run against the answer key.

Usage (from the repo root):
    uv run python -m evals.aml_agent_score --run evals/runs/<run_id> [--target-recall 0.9]
        [--out PATH] [--allow-remote] [--record]

This file is one of the few allowed label readers (see evals/test_label_readers.py and
docs/decisions/0005). It reads the labels itself at scoring time, so the runner and the agent
never see them. It is strictly read-only (SELECT only). The database URL comes from the
environment only and is never printed, and a remote database is refused unless
--allow-remote is passed. The report holds counts and rates only, never per-alert truth.

With --record (off by default) the report is also written to eval_results through
HARNESS_WRITE_DATABASE_URL (same local-only check). The run must have been recorded by the
runner with --record first. The labels are read through DATABASE_URL and never written.

Metrics follow docs/plans/agent-loop.md section 8. Every metric is split into early alerts
(window ends on or before 2022-09-10) and late ones (decision 0006). Early is the headline.

The first half of the file is pure (no database code). The database part is at the bottom.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, select

from evals.aml_rules_score import (
    Label,
    ScoredAlert,
    _alert_family,
    classify_alert,
    load_alerts_and_labels,
    score,
)
from evals.run_aml_agent import (
    MANIFEST_FILE,
    RESULTS_FILE,
    SetupError,
    check_target,
    target_text,
)
from harness.db.models import Alert
from harness.db.session import DatabaseConnectionError, get_engine, run_safely
from harness.records import WRITE_URL_ENV, eval_run_exists, get_write_engine, record_eval_result
from harness.trace import CaseResult
from harness.verifiers.base import VerifierResult

LATE_CUTOFF = date(2022, 9, 10)  # decision 0006
DEFAULT_TARGET_RECALL = 0.9
ECE_BINS = 5
INDICATIVE_BELOW = 100
# Rank score of a human review case: above any confidence, so these are flagged first.
HUMAN_REVIEW_SCORE = 2.0
SCORE_FILE = "score.json"
SPLITS = ("early", "late", "all")

GROUNDING_RULE = (
    "A claim is grounded when the citation verifier reported no error about the claim and no "
    "error about any transfer it cites (the transfer exists, appeared in a tool result this "
    "run, and cited amounts and currencies match the stored ones). Claims come from proposed "
    "results and from verifier_failed results that kept findings. A citation result that "
    "failed with an error naming no claim or transfer (a crashed verifier) makes every claim "
    "of that result ungrounded. verifier_error results (an infrastructure failure inside a "
    "verifier) count no claims."
)
NOTES: tuple[str, ...] = (
    "Truth is strict (at least half of the alert's transfers are laundering). Loose (at least "
    "one) is reported alongside.",
    "Baseline is the rules alone on the same alerts: every alert escalated.",
    "Human review cases count as escalated in agent_with_human_review. agent_proposed_only "
    "drops them.",
    "fp_reduction.at_target_recall uses the highest confidence cutoff that keeps strict recall "
    "at or above the target (the smallest flagged set). Human review cases rank above every "
    "confidence; a threshold above 1.0 means only human review cases are flagged.",
    "Scheme recall feeds the escalated alerts (human review included) into the rules scorer, "
    "over every scheme in the slice, so small runs show small scheme recall.",
    "Calibration uses proposed results only. ECE has 5 equal width bins and is indicative when "
    "n is below 100.",
    "Early alerts end on or before 2022-09-10 and are the headline. Late alerts sit in a window "
    "where normal traffic almost stops (decision 0006).",
    "Review (maker-checker) compares the run with the same run as if the checker had not run: "
    "a case the checker rejected or failed counts with the maker's own recommendation. It is "
    "indicative on small sets.",
)
REVIEW_REASONS = ("reviewer_rejected", "reviewer_failed")

_TXN_ERROR = re.compile(r"^txn (\d+):")
_CLAIM_ERROR = re.compile(r"^claim (\d+):")


@dataclass(frozen=True)
class Outcome:
    """What the scorer needs from one CaseResult."""

    alert_id: str
    status: str
    reason: str | None
    recommendation: str | None
    confidence: float | None
    typology: str | None
    claims_total: int
    claims_grounded: int
    cost_usd: Decimal
    wall_ms: int
    tool_steps: int
    review_verdict: str | None = None  # approve, reject, failed, or None when not reviewed

    @property
    def human_review(self) -> bool:
        return self.status == "human_review"

    @property
    def escalated(self) -> bool:
        """Escalated with human review counted as escalated (a human looks at it)."""
        return self.human_review or self.recommendation == "escalate"

    @property
    def escalated_without_review(self) -> bool:
        """escalated as if the checker had not run: a reviewer reason falls back to the
        maker's own recommendation."""
        if self.reason in REVIEW_REASONS:
            return self.recommendation == "escalate"
        return self.escalated

    @property
    def rank_score(self) -> float:
        if self.human_review or self.confidence is None:
            return HUMAN_REVIEW_SCORE
        return self.confidence


@dataclass(frozen=True)
class Truth:
    alert: ScoredAlert
    strict: bool
    loose: bool
    family: str | None
    late: bool


Pair = tuple[Outcome, Truth]


def _ratio(num: int | float, den: int | float) -> float | None:
    return None if den == 0 else round(num / den, 6)


def _claim_ids(claim: Mapping[str, Any]) -> set[int]:
    ids = {int(t) for t in claim.get("txn_ids", [])}
    ids.update(int(a["txn_id"]) for a in claim.get("amounts", []))
    return ids


def claim_grounding(
    findings: Mapping[str, Any] | None, verifier_results: Sequence[VerifierResult]
) -> tuple[int, int]:
    """(claims counted, claims grounded) under GROUNDING_RULE. Results with no findings or no
    citation result count no claims."""
    if findings is None:
        return 0, 0
    citations = next((r for r in verifier_results if r.name == "citations"), None)
    if citations is None:
        return 0, 0
    claims: list[Mapping[str, Any]] = list(findings.get("claims", []))
    if citations.passed:
        return len(claims), len(claims)
    bad_txns: set[int] = set()
    bad_claims: set[int] = set()
    for error in citations.errors:
        claim_match = _CLAIM_ERROR.match(error)
        txn_match = _TXN_ERROR.match(error)
        if claim_match:
            bad_claims.add(int(claim_match.group(1)))
        elif txn_match:
            bad_txns.add(int(txn_match.group(1)))
        else:
            return len(claims), 0
    if not citations.errors:
        return len(claims), 0
    grounded = sum(
        1
        for i, claim in enumerate(claims)
        if i not in bad_claims and not (_claim_ids(claim) & bad_txns)
    )
    return len(claims), grounded


def outcome_from_result(result: CaseResult) -> Outcome:
    findings = result.findings
    if result.reason == "verifier_error":
        total, grounded = 0, 0  # the checks did not really run
    else:
        total, grounded = claim_grounding(findings, result.verifier_results)
    return Outcome(
        alert_id=result.case_id,
        status=result.status,
        reason=result.reason,
        recommendation=None if findings is None else findings.get("recommendation"),
        confidence=None if findings is None else float(findings["confidence"]),
        typology=None if findings is None else findings.get("typology"),
        claims_total=total,
        claims_grounded=grounded,
        cost_usd=Decimal(result.cost_usd),
        wall_ms=result.wall_ms,
        tool_steps=result.tool_steps,
        review_verdict=None if result.review is None else (result.review.verdict or "failed"),
    )


def build_truths(
    alerts: Mapping[str, ScoredAlert],
    labels: Mapping[int, Label],
    window_ends: Mapping[str, datetime],
) -> dict[str, Truth]:
    truths: dict[str, Truth] = {}
    for alert_id, alert in alerts.items():
        strict, loose = classify_alert(alert, labels)
        truths[alert_id] = Truth(
            alert=alert,
            strict=strict,
            loose=loose,
            family=_alert_family(alert, labels),
            late=window_ends[alert_id].date() > LATE_CUTOFF,
        )
    return truths


def confusion(
    pairs: Sequence[Pair], flagged: Callable[[Outcome], bool], positive: Callable[[Truth], bool]
) -> dict[str, Any]:
    tp = sum(1 for o, t in pairs if flagged(o) and positive(t))
    fp = sum(1 for o, t in pairs if flagged(o) and not positive(t))
    fn = sum(1 for o, t in pairs if not flagged(o) and positive(t))
    tn = len(pairs) - tp - fp - fn
    return {
        "n": len(pairs),
        "flagged": tp + fp,
        "positives": tp + fn,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
    }


def _strict(t: Truth) -> bool:
    return t.strict


def _loose(t: Truth) -> bool:
    return t.loose


def _escalated(o: Outcome) -> bool:
    return o.escalated


def _recommends_escalate(o: Outcome) -> bool:
    return o.recommendation == "escalate"


def _escalated_without_review(o: Outcome) -> bool:
    return o.escalated_without_review


def _delta(after: float | None, before: float | None) -> float | None:
    return None if after is None or before is None else round(after - before, 6)


def review_metrics(pairs: Sequence[Pair]) -> dict[str, Any]:
    """Reviewer reject rate and how the review changed precision and recall (strict truth,
    human review counted as escalated). A rejected maker escalation that was a false positive
    stays flagged for a human, so the review moves only rejected closes and failures on the
    confusion matrix; the counts below show both."""
    reviewed = [(o, t) for o, t in pairs if o.review_verdict is not None]
    verdicts = Counter(str(o.review_verdict) for o, _ in reviewed)
    rejected = [(o, t) for o, t in reviewed if o.review_verdict == "reject"]
    with_review = confusion(pairs, _escalated, _strict)
    without = confusion(pairs, _escalated_without_review, _strict)
    return {
        "reviewed": len(reviewed),
        "approved": verdicts.get("approve", 0),
        "rejected": verdicts.get("reject", 0),
        "failed": verdicts.get("failed", 0),
        "reject_rate": _ratio(verdicts.get("reject", 0), len(reviewed)),
        "rejected_escalations": {
            "true_positive": sum(
                1 for o, t in rejected if o.recommendation == "escalate" and t.strict
            ),
            "false_positive": sum(
                1 for o, t in rejected if o.recommendation == "escalate" and not t.strict
            ),
        },
        "rejected_closes": {
            "missed_positive": sum(
                1 for o, t in rejected if o.recommendation == "close" and t.strict
            ),
            "true_negative": sum(
                1 for o, t in rejected if o.recommendation == "close" and not t.strict
            ),
        },
        "without_review": {"precision": without["precision"], "recall": without["recall"]},
        "with_review": {"precision": with_review["precision"], "recall": with_review["recall"]},
        "precision_change": _delta(with_review["precision"], without["precision"]),
        "recall_change": _delta(with_review["recall"], without["recall"]),
        "indicative": True,
    }


def baseline_metrics(pairs: Sequence[Pair]) -> dict[str, Any]:
    """Rules only: every alert escalated."""
    n = len(pairs)
    true_strict = sum(1 for _, t in pairs if t.strict)
    true_loose = sum(1 for _, t in pairs if t.loose)
    return {
        "raised": n,
        "true_strict": true_strict,
        "true_loose": true_loose,
        "false_strict": n - true_strict,
        "precision_strict": _ratio(true_strict, n),
        "precision_loose": _ratio(true_loose, n),
        "recall_strict": 1.0 if true_strict else None,
    }


def _fp_reduction(fp: int, baseline_fp: int) -> float | None:
    return None if baseline_fp == 0 else round(1 - fp / baseline_fp, 6)


def fp_reduction_at_recall(pairs: Sequence[Pair], target_recall: float) -> dict[str, Any]:
    """Highest rank-score cutoff whose flagged set keeps strict recall at or above the
    target, and the false positive reduction there against escalating everything."""
    positives = sum(1 for _, t in pairs if t.strict)
    baseline_fp = len(pairs) - positives
    out: dict[str, Any] = {
        "target_recall": target_recall,
        "threshold": None,
        "flagged": None,
        "tp": None,
        "fp": None,
        "recall": None,
        "baseline_fp": baseline_fp,
        "fp_reduction": None,
    }
    if positives == 0:
        return out
    for threshold in sorted({o.rank_score for o, _ in pairs}, reverse=True):
        tp = sum(1 for o, t in pairs if o.rank_score >= threshold and t.strict)
        if tp / positives >= target_recall:
            fp = sum(1 for o, t in pairs if o.rank_score >= threshold and not t.strict)
            out.update(
                threshold=threshold,
                flagged=tp + fp,
                tp=tp,
                fp=fp,
                recall=_ratio(tp, positives),
                fp_reduction=_fp_reduction(fp, baseline_fp),
            )
            return out
    return out  # unreachable: the lowest cutoff flags everything, recall 1.0


def fp_reduction_at_decisions(pairs: Sequence[Pair]) -> dict[str, Any]:
    c = confusion(pairs, _escalated, _strict)
    baseline_fp = c["fp"] + c["tn"]
    return {
        "flagged": c["flagged"],
        "tp": c["tp"],
        "fp": c["fp"],
        "recall": c["recall"],
        "baseline_fp": baseline_fp,
        "fp_reduction": _fp_reduction(c["fp"], baseline_fp),
    }


def typology_metrics(pairs: Sequence[Pair]) -> dict[str, Any]:
    pos = [(o, t) for o, t in pairs if t.strict]
    pos_typed = [(o, t) for o, t in pos if o.typology is not None]
    correct = sum(1 for o, t in pos_typed if o.typology == t.family)
    neg_typed = [o for o, t in pairs if not t.strict and o.typology is not None]
    none = sum(1 for o in neg_typed if o.typology == "NONE")
    return {
        "strict_positives": len(pos),
        "strict_positives_with_typology": len(pos_typed),
        "correct": correct,
        "accuracy": _ratio(correct, len(pos_typed)),
        "negatives_with_typology": len(neg_typed),
        "none_on_negatives": none,
        "none_rate_on_negatives": _ratio(none, len(neg_typed)),
    }


def grounding_metrics(pairs: Sequence[Pair]) -> dict[str, Any]:
    total = sum(o.claims_total for o, _ in pairs)
    grounded = sum(o.claims_grounded for o, _ in pairs)
    return {"claims": total, "grounded": grounded, "rate": _ratio(grounded, total)}


def calibration(pairs: Sequence[Pair], bins: int = ECE_BINS) -> dict[str, Any]:
    """Expected calibration error over proposed results, equal width bins on confidence."""
    points = [
        (o.confidence, t.strict)
        for o, t in pairs
        if o.status == "proposed" and o.confidence is not None
    ]
    n = len(points)
    rows: list[dict[str, Any]] = []
    ece = 0.0
    for b in range(bins):
        inside = [(c, s) for c, s in points if min(int(c * bins), bins - 1) == b]
        mean_conf = sum(c for c, _ in inside) / len(inside) if inside else None
        pos_rate = sum(1 for _, s in inside if s) / len(inside) if inside else None
        if mean_conf is not None and pos_rate is not None:
            ece += len(inside) / n * abs(pos_rate - mean_conf)
        rows.append(
            {
                "lower": round(b / bins, 6),
                "upper": round((b + 1) / bins, 6),
                "n": len(inside),
                "mean_confidence": None if mean_conf is None else round(mean_conf, 6),
                "positive_rate": None if pos_rate is None else round(pos_rate, 6),
            }
        )
    return {
        "n": n,
        "ece": round(ece, 6) if n else None,
        "bins": rows,
        "indicative": n < INDICATIVE_BELOW,
    }


def effort_metrics(pairs: Sequence[Pair]) -> dict[str, Any]:
    n = len(pairs)
    total_cost = sum((o.cost_usd for o, _ in pairs), Decimal(0))
    walls = [o.wall_ms for o, _ in pairs]
    steps = [o.tool_steps for o, _ in pairs]
    return {
        "total_cost_usd": str(total_cost),
        "cost_per_alert_usd": None if n == 0 else str(total_cost / n),
        "latency_ms_mean": _ratio(sum(walls), n),
        "latency_ms_max": max(walls) if walls else None,
        "tool_steps_mean": _ratio(sum(steps), n),
        "tool_steps_max": max(steps) if steps else None,
    }


def scheme_recall(pairs: Sequence[Pair], labels: Mapping[int, Label]) -> dict[str, Any]:
    escalated = [t.alert for o, t in pairs if o.escalated]
    every = [t.alert for _, t in pairs]
    return {
        "agent": score(escalated, labels)[0]["overall"]["recall"],
        "baseline": score(every, labels)[0]["overall"]["recall"],
    }


def subset_metrics(
    pairs: Sequence[Pair], labels: Mapping[int, Label], target_recall: float
) -> dict[str, Any]:
    proposed = [(o, t) for o, t in pairs if not o.human_review]
    reviews = [o for o, _ in pairs if o.human_review]
    return {
        "alerts": len(pairs),
        "baseline": baseline_metrics(pairs),
        "agent_with_human_review": {
            "strict": confusion(pairs, _escalated, _strict),
            "loose": confusion(pairs, _escalated, _loose),
        },
        "agent_proposed_only": {
            "strict": confusion(proposed, _recommends_escalate, _strict),
            "loose": confusion(proposed, _recommends_escalate, _loose),
        },
        "human_review": {
            "count": len(reviews),
            "rate": _ratio(len(reviews), len(pairs)),
            "reasons": dict(sorted(Counter(str(o.reason) for o in reviews).items())),
        },
        "fp_reduction": {
            "at_target_recall": fp_reduction_at_recall(pairs, target_recall),
            "at_agent_decisions": fp_reduction_at_decisions(pairs),
        },
        "scheme_recall": scheme_recall(pairs, labels),
        "typology": typology_metrics(pairs),
        "grounding": grounding_metrics(pairs),
        "calibration": calibration(pairs),
        "effort": effort_metrics(pairs),
        "review": review_metrics(pairs),
    }


def score_run(
    manifest: Mapping[str, Any],
    results: Sequence[CaseResult],
    truths: Mapping[str, Truth],
    labels: Mapping[int, Label],
    target_recall: float = DEFAULT_TARGET_RECALL,
) -> dict[str, Any]:
    missing = sorted({r.case_id for r in results} - set(truths))
    if missing:
        raise ValueError(f"{len(missing)} alert(s) in the run are not in the database")
    pairs = [(outcome_from_result(r), truths[r.case_id]) for r in results]
    by_split: dict[str, list[Pair]] = {
        "early": [p for p in pairs if not p[1].late],
        "late": [p for p in pairs if p[1].late],
        "all": pairs,
    }
    return {
        "run_id": manifest.get("run_id"),
        "dataset": manifest.get("dataset"),
        "model": manifest.get("model"),
        "prompt": manifest.get("prompt"),
        "target_recall": target_recall,
        "late_cutoff": LATE_CUTOFF.isoformat(),
        "headline": "early",
        "splits": {name: subset_metrics(by_split[name], labels, target_recall) for name in SPLITS},
        "grounding_rule": GROUNDING_RULE,
        "notes": list(NOTES),
    }


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=2) + "\n"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _split_lines(name: str, m: Mapping[str, Any]) -> list[str]:
    agent = m["agent_with_human_review"]["strict"]
    prop = m["agent_proposed_only"]["strict"]
    fp = m["fp_reduction"]
    cal = m["calibration"]
    eff = m["effort"]
    rev = m["review"]
    return [
        f"{name}: alerts {m['alerts']}, baseline precision "
        f"{_fmt(m['baseline']['precision_strict'])}",
        f"  agent (human review as escalated): precision {_fmt(agent['precision'])}, "
        f"recall {_fmt(agent['recall'])}; proposed only: precision {_fmt(prop['precision'])}, "
        f"recall {_fmt(prop['recall'])}",
        f"  human review rate {_fmt(m['human_review']['rate'])} {m['human_review']['reasons']}",
        f"  fp reduction at recall {fp['at_target_recall']['target_recall']}: "
        f"{_fmt(fp['at_target_recall']['fp_reduction'])}; at agent decisions: "
        f"{_fmt(fp['at_agent_decisions']['fp_reduction'])}",
        f"  typology accuracy {_fmt(m['typology']['accuracy'])}, grounding "
        f"{_fmt(m['grounding']['rate'])}, ece {_fmt(cal['ece'])}"
        f"{' (indicative)' if cal['indicative'] else ''}",
        f"  cost per alert {eff['cost_per_alert_usd']}, latency ms {_fmt(eff['latency_ms_mean'])}, "
        f"tool steps mean {_fmt(eff['tool_steps_mean'])} max {eff['tool_steps_max']}",
        f"  review: {rev['reviewed']} reviewed, reject rate {_fmt(rev['reject_rate'])}, "
        f"precision change {_fmt(rev['precision_change'])}, recall change "
        f"{_fmt(rev['recall_change'])} (indicative)",
    ]


def render_summary(report: Mapping[str, Any]) -> str:
    lines = [f"run: {report['run_id']}", "headline is early (window ends on or before 2022-09-10)"]
    for name in SPLITS:
        lines += _split_lines(name, report["splits"][name])
    return "\n".join(lines)


def read_run(run_dir: Path) -> tuple[dict[str, Any], list[CaseResult]]:
    try:
        manifest = json.loads((run_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        lines = (run_dir / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise SetupError(f"cannot read the run files in {run_dir}") from None
    results = [CaseResult.model_validate_json(line) for line in lines if line.strip()]
    return manifest, results


# ---------------------------------------------------------------------------------------------
# Database part. SELECT statements only.
# ---------------------------------------------------------------------------------------------


def load_truths(
    conn: Connection, ruleset_version: str, alert_ids: Sequence[str]
) -> tuple[dict[str, Truth], dict[int, Label]]:
    """Truth for exactly the given alerts, plus every label (for scheme recall). SELECT only."""
    wanted = set(alert_ids)
    alerts, labels = load_alerts_and_labels(conn, ruleset_version)
    by_id = {a.alert_id: a for a in alerts if a.alert_id in wanted}
    rows = conn.execute(
        select(Alert.alert_id, Alert.window_end).where(Alert.ruleset_version == ruleset_version)
    ).all()
    ends = {alert_id: end for alert_id, end in rows if alert_id in wanted}
    return build_truths(by_id, labels, ends), labels


def score_run_dir(
    conn: Connection, run_dir: Path, target_recall: float = DEFAULT_TARGET_RECALL
) -> dict[str, Any]:
    manifest, results = read_run(run_dir)
    ruleset_version = manifest["dataset"]["ruleset_version"]
    truths, labels = load_truths(conn, ruleset_version, [r.case_id for r in results])
    return score_run(manifest, results, truths, labels, target_recall)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.aml_agent_score")
    parser.add_argument("--run", type=Path, required=True, help="run directory")
    parser.add_argument("--target-recall", type=float, default=DEFAULT_TARGET_RECALL)
    parser.add_argument("--out", type=Path, default=None, help="report path")
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow a database that is not local"
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"also write the report to eval_results through {WRITE_URL_ENV}",
    )
    return parser


def record_report(
    report: Mapping[str, Any], target_recall: float, allow_remote: bool = False
) -> None:
    """Write the report to eval_results through the write engine. Raises SetupError when the
    eval run is not recorded."""
    eval_run_id = str(report["run_id"])
    engine = get_write_engine(allow_remote=allow_remote)
    try:

        def work() -> bool:
            with engine.begin() as conn:
                if not eval_run_exists(conn, eval_run_id):
                    return False
                record_eval_result(conn, eval_run_id, report, target_recall)
                return True

        if not run_safely(work):
            raise SetupError(
                f"eval run {eval_run_id} is not recorded; run the runner with --record first"
            )
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out: Path = args.out or args.run / SCORE_FILE
    url = os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise SetupError("DATABASE_URL is not set")
        if not 0.0 < args.target_recall <= 1.0:
            raise SetupError("--target-recall must be in (0, 1]")
        print(target_text(url))
        check_target(url, args.allow_remote)
        write_url = os.environ.get(WRITE_URL_ENV)
        if args.record:
            if not write_url:
                raise SetupError(f"--record needs {WRITE_URL_ENV} to be set")
            print("write " + target_text(write_url))
            check_target(write_url, args.allow_remote, WRITE_URL_ENV)
        engine = get_engine()
        try:

            def work() -> dict[str, Any]:
                with engine.connect() as conn:
                    return score_run_dir(conn, args.run, args.target_recall)

            report = run_safely(work)
        finally:
            engine.dispose()
        if args.record:
            record_report(report, args.target_recall, args.allow_remote)
    except (SetupError, DatabaseConnectionError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(render_summary(report))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_json(report), encoding="utf-8")
    print(f"report written: {out}")
    if args.record:
        print(f"recorded: eval result for {report['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
