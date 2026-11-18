"""Pure tests for the agent scorer on hand built results and labels. No database."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evals.aml_agent_score import (
    HUMAN_REVIEW_SCORE,
    Outcome,
    Pair,
    Truth,
    baseline_metrics,
    build_truths,
    calibration,
    claim_grounding,
    confusion,
    fp_reduction_at_decisions,
    fp_reduction_at_recall,
    grounding_metrics,
    outcome_from_result,
    render_summary,
    review_metrics,
    score_run,
    subset_metrics,
    typology_metrics,
)
from evals.aml_rules_score import Label, ScoredAlert, _alert_metrics
from harness.loop import human_review_without_run
from harness.trace import CaseResult
from harness.verifiers.base import VerifierResult

BASELINE_FILE = Path(__file__).parent / "baselines" / "aml_rules_v1.json"


def _o(
    alert_id: str,
    rec: str | None = "close",
    conf: float | None = 0.1,
    typ: str | None = "NONE",
    *,
    status: str = "proposed",
    reason: str | None = None,
    claims: tuple[int, int] = (1, 1),
    cost: str = "0",
    wall: int = 10,
    steps: int = 2,
    verdict: str | None = None,
) -> Outcome:
    return Outcome(
        alert_id=alert_id,
        status=status,
        reason=reason,
        recommendation=rec,
        confidence=conf,
        typology=typ,
        claims_total=claims[0],
        claims_grounded=claims[1],
        cost_usd=Decimal(cost),
        wall_ms=wall,
        tool_steps=steps,
        review_verdict=verdict,
    )


def _t(
    alert_id: str, strict: bool, family: str | None = None, *, late: bool = False, txn: int = 1
) -> Truth:
    alert = ScoredAlert(alert_id, "fan_out", frozenset({txn}))
    return Truth(alert=alert, strict=strict, loose=strict, family=family, late=late)


def _example() -> list[Pair]:
    """3 strict positives (A1, A2, A6) and 3 negatives."""
    return [
        (_o("A1", "escalate", 0.9, "FAN-OUT"), _t("A1", True, "FAN-OUT")),
        (_o("A2", "close", 0.3, "NONE"), _t("A2", True, "CYCLE")),
        (_o("A3", "escalate", 0.7, "STACK"), _t("A3", False)),
        (_o("A4", "close", 0.2, "NONE"), _t("A4", False)),
        (_o("A5", "close", 0.1, "NONE"), _t("A5", False)),
        (
            _o("A6", None, None, None, status="human_review", reason="limit_steps", claims=(0, 0)),
            _t("A6", True, "FAN-IN"),
        ),
    ]


def _strict(t: Truth) -> bool:
    return t.strict


def test_precision_and_recall_with_and_without_human_review() -> None:
    pairs = _example()
    with_hr = confusion(pairs, lambda o: o.escalated, _strict)
    assert (with_hr["tp"], with_hr["fp"], with_hr["fn"], with_hr["tn"]) == (2, 1, 1, 2)
    assert with_hr["precision"] == pytest.approx(0.666667)
    assert with_hr["recall"] == pytest.approx(0.666667)
    m = subset_metrics(pairs, {}, 0.9)
    prop = m["agent_proposed_only"]["strict"]
    assert prop["n"] == 5 and prop["precision"] == 0.5 and prop["recall"] == 0.5
    hr = m["human_review"]
    assert hr["count"] == 1 and hr["rate"] == pytest.approx(0.166667)
    assert hr["reasons"] == {"limit_steps": 1}


def test_review_metrics_hand_computed() -> None:
    rejected = {"status": "human_review", "reason": "reviewer_rejected", "verdict": "reject"}
    pairs = [
        (_o("A1", "escalate", 0.9, "FAN-OUT", verdict="approve"), _t("A1", True)),
        (_o("A2", "escalate", 0.7, "STACK", **rejected), _t("A2", False)),  # type: ignore[arg-type]
        (_o("A3", "close", 0.3, "NONE", **rejected), _t("A3", True)),  # type: ignore[arg-type]
        (_o("A4", "close", 0.1, "NONE", verdict="approve"), _t("A4", False)),
        (
            _o(
                "A5",
                "close",
                0.2,
                "NONE",
                status="human_review",
                reason="reviewer_failed",
                verdict="failed",
            ),
            _t("A5", False),
        ),
        (_o("A6", "close", 0.1, "NONE"), _t("A6", False)),  # not reviewed
    ]
    m = review_metrics(pairs)
    assert (m["reviewed"], m["approved"], m["rejected"], m["failed"]) == (5, 2, 2, 1)
    assert m["reject_rate"] == 0.4
    assert m["rejected_escalations"] == {"true_positive": 0, "false_positive": 1}
    assert m["rejected_closes"] == {"missed_positive": 1, "true_negative": 0}
    # Without review: flagged A1, A2 (tp 1, fp 1, fn 1). With review: A1, A2, A3, A5.
    assert m["without_review"] == {"precision": 0.5, "recall": 0.5}
    assert m["with_review"] == {"precision": 0.5, "recall": 1.0}
    assert m["precision_change"] == 0.0 and m["recall_change"] == 0.5
    assert m["indicative"] is True


def test_human_review_ranks_above_every_confidence() -> None:
    hr = _o("X", None, None, None, status="human_review", reason="verifier_failed")
    assert hr.escalated and hr.rank_score == HUMAN_REVIEW_SCORE
    # A verifier_failed result that kept findings still counts as human review.
    kept = _o("Y", "close", 0.1, "NONE", status="human_review", reason="verifier_failed")
    assert kept.escalated and kept.rank_score == HUMAN_REVIEW_SCORE


def test_baseline_escalates_everything() -> None:
    b = baseline_metrics(_example())
    assert b["raised"] == 6 and b["true_strict"] == 3 and b["precision_strict"] == 0.5
    assert b["recall_strict"] == 1.0


def test_fp_reduction_at_target_recall_hand_computed() -> None:
    # Ranked: A6 (2.0), A1 0.9, A3 0.7, A2 0.3, A4 0.2, A5 0.1. Recall 0.9 needs all three
    # positives, so the cutoff is 0.3: flagged A6, A1, A3, A2 with one false positive.
    r = fp_reduction_at_recall(_example(), 0.9)
    assert r["threshold"] == 0.3 and r["flagged"] == 4 and r["fp"] == 1 and r["recall"] == 1.0
    assert r["baseline_fp"] == 3 and r["fp_reduction"] == pytest.approx(0.666667)
    # Recall 0.6 is reached at 0.9 (A6 and A1), with no false positive.
    r = fp_reduction_at_recall(_example(), 0.6)
    assert r["threshold"] == 0.9 and r["fp"] == 0 and r["fp_reduction"] == 1.0
    d = fp_reduction_at_decisions(_example())
    assert d["fp"] == 1 and d["fp_reduction"] == pytest.approx(0.666667)


def test_fp_reduction_without_positives_is_none() -> None:
    pairs = [(_o("A4"), _t("A4", False))]
    assert fp_reduction_at_recall(pairs, 0.9)["fp_reduction"] is None


def test_ece_hand_computed() -> None:
    # Proposed only: bins [0,0.2): 0.1 neg -> gap 0.1; [0.2,0.4): 0.2 neg, 0.3 pos -> mean 0.25,
    # rate 0.5, gap 0.25 (n 2); [0.6,0.8): 0.7 neg -> gap 0.7; [0.8,1]: 0.9 pos -> gap 0.1.
    # ECE = (0.1 + 2 * 0.25 + 0.7 + 0.1) / 5 = 0.28.
    c = calibration(_example())
    assert c["n"] == 5 and c["ece"] == pytest.approx(0.28) and c["indicative"] is True
    assert [b["n"] for b in c["bins"]] == [1, 2, 0, 1, 1]
    assert c["bins"][2]["mean_confidence"] is None
    assert c["bins"][4]["upper"] == 1.0


def test_confidence_one_falls_in_last_bin() -> None:
    c = calibration([(_o("A", "escalate", 1.0, "CYCLE"), _t("A", True))])
    assert [b["n"] for b in c["bins"]] == [0, 0, 0, 0, 1] and c["ece"] == 0.0


def test_typology_accuracy_on_strict_positives_only() -> None:
    t = typology_metrics(_example())
    assert t["strict_positives"] == 3 and t["strict_positives_with_typology"] == 2
    assert t["correct"] == 1 and t["accuracy"] == 0.5
    assert t["negatives_with_typology"] == 3 and t["none_rate_on_negatives"] == pytest.approx(
        0.666667
    )


_FINDINGS: dict[str, Any] = {
    "claims": [
        {"txn_ids": [1], "amounts": [{"txn_id": 1}]},
        {"txn_ids": [2], "amounts": []},
    ]
}


def _cit(passed: bool, errors: list[str]) -> list[VerifierResult]:
    return [VerifierResult(name="citations", passed=passed, errors=errors)]


def test_claim_grounding_from_verifier_errors() -> None:
    assert claim_grounding(_FINDINGS, _cit(True, [])) == (2, 2)
    assert claim_grounding(_FINDINGS, _cit(False, ["txn 2: does not exist"])) == (2, 1)
    assert claim_grounding(
        _FINDINGS, _cit(False, ["txn 1: cited paid amount does not match the stored amount"])
    ) == (2, 1)
    assert claim_grounding(_FINDINGS, _cit(False, ["claim 1: txn 9: amount cited"])) == (2, 1)
    assert claim_grounding(_FINDINGS, _cit(False, ["verifier error (KeyError)"])) == (2, 0)
    assert claim_grounding(_FINDINGS, []) == (0, 0)
    assert claim_grounding(None, _cit(True, [])) == (0, 0)


def test_grounding_rate_sums_claims() -> None:
    pairs = [(_o("A", claims=(2, 1)), _t("A", False)), (_o("B", claims=(3, 3)), _t("B", True))]
    assert grounding_metrics(pairs) == {"claims": 5, "grounded": 4, "rate": 0.8}


def _hr(alert_id: str) -> CaseResult:
    return human_review_without_run(alert_id, "aml", "limit_steps")


def _proposed(alert_id: str, rec: str, conf: float, typ: str, txn: int) -> CaseResult:
    findings = {
        "alert_id": alert_id,
        "recommendation": rec,
        "confidence": conf,
        "typology": typ,
        "narrative": "n",
        "claims": [{"statement": "s", "txn_ids": [txn], "amounts": []}],
        "figures": [],
    }
    body = _hr(alert_id).model_dump(mode="json")
    body.update(
        status="proposed",
        reason=None,
        findings=findings,
        verifier_results=[{"name": "citations", "passed": True, "errors": []}],
        cost_usd="0.5",
        wall_ms=100,
        tool_steps=3,
    )
    return CaseResult.model_validate(body)


def test_outcome_from_result() -> None:
    o = outcome_from_result(_proposed("AL-000000000001", "escalate", 0.8, "FAN-OUT", 1))
    assert (o.status, o.recommendation, o.confidence, o.typology) == (
        "proposed",
        "escalate",
        0.8,
        "FAN-OUT",
    )
    assert (o.claims_total, o.claims_grounded, o.cost_usd) == (1, 1, Decimal("0.5"))
    h = outcome_from_result(_hr("AL-000000000002"))
    assert h.escalated and h.recommendation is None and h.claims_total == 0


def test_score_run_splits_early_and_late() -> None:
    labels = {
        1: Label(True, 7, "FAN-OUT"),
        2: Label(True, 7, "FAN-OUT"),
        3: Label(False, None, None),
        4: Label(True, 8, "CYCLE"),
    }
    alerts = {
        "AL-000000000001": ScoredAlert("AL-000000000001", "fan_out", frozenset({1, 2})),
        "AL-000000000002": ScoredAlert("AL-000000000002", "fan_out", frozenset({3})),
        "AL-000000000003": ScoredAlert("AL-000000000003", "cycle", frozenset({4})),
    }
    ends = {
        "AL-000000000001": datetime(2022, 9, 10, 23, 0),
        "AL-000000000002": datetime(2022, 9, 5),
        "AL-000000000003": datetime(2022, 9, 11, 1, 0),
    }
    truths = build_truths(alerts, labels, ends)
    assert [truths[a].late for a in sorted(truths)] == [False, False, True]
    results = [
        _proposed("AL-000000000001", "escalate", 0.9, "FAN-OUT", 1),
        _proposed("AL-000000000002", "close", 0.1, "NONE", 3),
        _hr("AL-000000000003"),
    ]
    report = score_run({"run_id": "r1"}, results, truths, labels, 0.9)
    early, late, every = (report["splits"][s] for s in ("early", "late", "all"))
    assert report["headline"] == "early" and report["late_cutoff"] == "2022-09-10"
    assert (early["alerts"], late["alerts"], every["alerts"]) == (2, 1, 3)
    assert early["agent_with_human_review"]["strict"]["precision"] == 1.0
    assert early["fp_reduction"]["at_agent_decisions"]["fp_reduction"] == 1.0
    assert early["typology"]["accuracy"] == 1.0
    assert early["scheme_recall"]["agent"]["all"]["caught"] == 1
    assert late["human_review"]["count"] == 1
    assert every["effort"]["total_cost_usd"] == "1.0"
    assert every["effort"]["tool_steps_max"] == 3
    # Counts and rates only: no alert id appears in the report.
    text = json.dumps(report)
    assert "AL-0000" not in text
    assert "headline is early" in render_summary(report)


def test_score_run_refuses_alerts_missing_from_the_database() -> None:
    with pytest.raises(ValueError, match="not in the database"):
        score_run({}, [_hr("AL-000000000009")], {}, {}, 0.9)


def test_baseline_matches_rules_scorer_alert_block_on_constructed_set() -> None:
    labels = {i: Label(i % 3 == 0, i, "FAN-OUT" if i % 3 == 0 else None) for i in range(1, 31)}
    alerts = [
        ScoredAlert(f"AL-{i:012x}", "fan_out", frozenset({i, i + 1, i + 2}))
        for i in range(1, 28, 2)
    ]
    ends = {a.alert_id: datetime(2022, 9, 1) for a in alerts}
    truths = build_truths({a.alert_id: a for a in alerts}, labels, ends)
    pairs = [(_o(a.alert_id), truths[a.alert_id]) for a in alerts]
    ours = baseline_metrics(pairs)
    rules = _alert_metrics(alerts, labels)
    for key in ("raised", "true_strict", "true_loose", "false_strict", "precision_strict"):
        assert ours[key] == rules[key], key


def test_baseline_file_numbers_are_the_known_ones() -> None:
    block = json.loads(BASELINE_FILE.read_text())["metrics"]["overall"]["alerts"]
    assert (block["raised"], block["true_strict"], block["precision_strict"]) == (171, 39, 0.22807)
