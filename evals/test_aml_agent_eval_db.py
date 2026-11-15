"""Runner and scorer end to end on the local test database with the scripted policy.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only; the
CLIs read DATABASE_URL, so it is set to the local test URL for the call."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from evals import aml_agent_score, run_aml_agent
from evals.aml_agent_score import Outcome, baseline_metrics, load_truths
from harness.db.models import Alert
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.rules.ruleset import V1
from modules.aml.store import delete_alerts_for_version, write_alerts
from tests_support.aml_db import local_url

EVAL_VERSION = "test-evals-agent"
BASELINE_FILE = Path(__file__).parent / "baselines" / "aml_rules_v1.json"


def _escalate(alert_id: str) -> Outcome:
    """Every alert escalated, as the rules baseline assumes."""
    return Outcome(alert_id, "proposed", None, "escalate", 0.9, None, 0, 0, Decimal(0), 0, 0)


@pytest.fixture(scope="module")
def eval_alert_ids(local_engine: Engine) -> Iterator[list[str]]:
    """The v1 rules written under a test ruleset version, removed afterwards."""
    delete_alerts_for_version(local_engine, EVAL_VERSION)
    try:
        write_alerts(local_engine, run_all(load_txns(local_engine), V1), EVAL_VERSION)
        with local_engine.connect() as conn:
            ids = conn.execute(
                select(Alert.alert_id)
                .where(Alert.ruleset_version == EVAL_VERSION)
                .order_by(Alert.alert_id)
            ).scalars()
            yield list(ids)
    finally:
        delete_alerts_for_version(local_engine, EVAL_VERSION)


def test_baseline_over_all_v1_alerts_matches_the_rules_baseline(
    local_engine: Engine, eval_alert_ids: list[str]
) -> None:
    with local_engine.connect() as conn:
        truths, _ = load_truths(conn, EVAL_VERSION, eval_alert_ids)
    pairs = [(_escalate(a), t) for a, t in truths.items()]
    ours = baseline_metrics(pairs)
    block = json.loads(BASELINE_FILE.read_text())["metrics"]["overall"]["alerts"]
    assert (ours["raised"], ours["true_strict"], ours["precision_strict"]) == (171, 39, 0.22807)
    for key in ("raised", "true_strict", "true_loose", "false_strict", "precision_strict"):
        assert ours[key] == block[key], key


def test_runner_and_scorer_end_to_end(
    eval_alert_ids: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = local_url()
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "alert_ids": eval_alert_ids[:3],
                "created_by": "test",
                "dataset": "test",
                "ruleset_version": EVAL_VERSION,
                "split_seed": 42,
                "version": "v0",
            }
        )
    )
    monkeypatch.setenv("DATABASE_URL", url)
    out_dir = tmp_path / "runs"
    code = run_aml_agent.main(
        [
            "--dataset",
            str(dataset),
            "--profile",
            "scripted",
            "--concurrency",
            "2",
            "--out-dir",
            str(out_dir),
        ]
    )
    stdout = capsys.readouterr().out
    assert code == 0, stdout
    assert "target: local database" in stdout
    last = stdout.strip().splitlines()[-1]
    assert last.startswith("run_dir=")
    run_dir = Path(last.removeprefix("run_dir="))
    assert run_dir.parent == out_dir

    lines = (run_dir / "results.jsonl").read_text().splitlines()
    assert len(lines) == 3
    results = [json.loads(line) for line in lines]
    assert {r["case_id"] for r in results} == set(eval_alert_ids[:3])
    assert all(r["status"] in ("proposed", "human_review") for r in results)

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["run_id"] == run_dir.name
    for key in (
        "git",
        "prompt",
        "model",
        "limits",
        "dataset",
        "started_at",
        "finished_at",
        "counts",
        "total_cost_usd",
    ):
        assert key in manifest, key
    assert set(manifest["git"]) == {"commit", "dirty"}
    assert manifest["prompt"]["version"] == "v1" and len(manifest["prompt"]["sha256"]) == 64
    assert manifest["model"] == {"profile": "scripted", "provider": "scripted", "model": "scripted"}
    assert manifest["dataset"]["ruleset_version"] == EVAL_VERSION
    assert len(manifest["dataset"]["sha256"]) == 64
    assert manifest["counts"]["total"] == 3
    assert manifest["total_cost_usd"] == "0"

    code = aml_agent_score.main(["--run", str(run_dir)])
    stdout = capsys.readouterr().out
    assert code == 0, stdout
    report = json.loads((run_dir / "score.json").read_text())
    assert set(report) >= {"splits", "headline", "late_cutoff", "target_recall", "notes"}
    assert set(report["splits"]) == {"early", "late", "all"}
    every = report["splits"]["all"]
    assert every["alerts"] == 3
    assert set(every) >= {
        "baseline",
        "agent_with_human_review",
        "agent_proposed_only",
        "human_review",
        "fp_reduction",
        "scheme_recall",
        "typology",
        "grounding",
        "calibration",
        "effort",
    }
    for alert_id in eval_alert_ids[:3]:
        assert alert_id not in json.dumps(report)
