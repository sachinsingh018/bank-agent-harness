"""Runner and scorer end to end on the local test database with the scripted policy.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only; the
CLIs read DATABASE_URL (and HARNESS_WRITE_DATABASE_URL with --record), so both are set to the
local test URL for the call."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from evals import aml_agent_score, run_aml_agent
from evals.aml_agent_score import Outcome, baseline_metrics, load_truths
from harness import records
from harness.db.models import Alert
from harness.db.records_models import AgentRun, CaseEvent, EvalResult, EvalRun, ModelCall
from harness.records import verify_chain
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.rules.ruleset import V1
from modules.aml.store import delete_alerts_for_version, write_alerts
from tests_support.aml_db import local_url
from tests_support.records_db import records_cleaned

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
    capfd: pytest.CaptureFixture[str],
) -> None:
    """capfd, not capsys: the tools server child needs a real stderr file descriptor, and
    the cases must really run (the scripted policy proposes every alert)."""
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
    stdout = capfd.readouterr().out
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
    assert [r["status"] for r in results] == ["proposed"] * 3, [r["reason"] for r in results]
    assert all(r["trace"]["model_calls"] and r["tool_steps"] >= 1 for r in results)

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
    assert manifest["prompt"]["version"] == "v2" and len(manifest["prompt"]["sha256"]) == 64
    assert manifest["model"] == {"profile": "scripted", "provider": "scripted", "model": "scripted"}
    assert manifest["dataset"]["ruleset_version"] == EVAL_VERSION
    assert len(manifest["dataset"]["sha256"]) == 64
    assert manifest["counts"]["total"] == 3
    assert manifest["total_cost_usd"] == "0"

    code = aml_agent_score.main(["--run", str(run_dir)])
    stdout = capfd.readouterr().out
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


def test_runner_and_scorer_record_end_to_end(
    local_engine: Engine,
    eval_alert_ids: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """--record writes eval_runs, agent_runs, model_calls, case_events and eval_results through
    HARNESS_WRITE_DATABASE_URL, here set to the local test URL. Rows are removed afterwards.
    capfd, not capsys: the tools server child needs a real stderr file descriptor."""
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
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", url)
    with records_cleaned(local_engine):
        code = run_aml_agent.main(
            [
                "--dataset",
                str(dataset),
                "--profile",
                "scripted",
                "--concurrency",
                "2",
                "--out-dir",
                str(tmp_path / "runs"),
                "--record",
            ]
        )
        stdout = capfd.readouterr().out
        assert code == 0, stdout
        assert "write target: local database" in stdout
        run_dir = Path(stdout.strip().splitlines()[-1].removeprefix("run_dir="))
        manifest = json.loads((run_dir / "manifest.json").read_text())
        eval_run_id = manifest["run_id"]
        assert manifest["records"] == {
            "eval_run_id": eval_run_id,
            "cases_recorded": 3,
            "cases_failed": 0,
            "eval_run_finished": True,
        }

        with local_engine.connect() as conn:
            run = conn.execute(select(EvalRun).where(EvalRun.eval_run_id == eval_run_id)).one()
            assert (run.dataset, run.dataset_version, run.profile) == ("test", "v0", "scripted")
            assert run.dataset_sha256 == manifest["dataset"]["sha256"]
            assert run.prompt_sha256 == manifest["prompt"]["sha256"]
            assert run.finished_at is not None and run.counts["total"] == 3
            assert run.total_cost_usd == Decimal(0)
            runs = conn.execute(select(AgentRun).where(AgentRun.eval_run_id == eval_run_id)).all()
            assert {r.case_id for r in runs} == set(eval_alert_ids[:3])
            assert [r.status for r in runs] == ["proposed"] * 3
            assert all(r.trace_path.endswith("results.jsonl") for r in runs)
            run_ids = [r.run_id for r in runs]
            calls = conn.execute(
                select(func.count()).where(ModelCall.run_id.in_(run_ids))
            ).scalar_one()
            assert calls >= 3
            events = conn.execute(select(CaseEvent).where(CaseEvent.run_id.in_(run_ids))).all()
            assert len(events) == 3
            assert verify_chain(conn).ok

        code = aml_agent_score.main(["--run", str(run_dir), "--record"])
        stdout = capfd.readouterr().out
        assert code == 0, stdout
        assert f"recorded: eval result for {eval_run_id}" in stdout
        with local_engine.connect() as conn:
            stored = conn.execute(
                select(EvalResult).where(EvalResult.eval_run_id == eval_run_id)
            ).one()
        assert stored.target_recall == Decimal("0.9")
        assert stored.score == json.loads((run_dir / "score.json").read_text())
        for alert_id in eval_alert_ids[:3]:
            assert alert_id not in json.dumps(stored.score)

        assert records.main(["verify"]) == 0
        assert "chain ok," in capfd.readouterr().out


def test_scorer_record_needs_a_recorded_run(
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
                "alert_ids": eval_alert_ids[:1],
                "created_by": "test",
                "dataset": "test",
                "ruleset_version": EVAL_VERSION,
                "split_seed": 42,
                "version": "v0",
            }
        )
    )
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", url)
    code = run_aml_agent.main(
        ["--dataset", str(dataset), "--profile", "scripted", "--out-dir", str(tmp_path / "r")]
    )
    stdout = capsys.readouterr().out
    assert code == 0, stdout
    run_dir = Path(stdout.strip().splitlines()[-1].removeprefix("run_dir="))
    assert aml_agent_score.main(["--run", str(run_dir), "--record"]) == 1
    assert "not recorded" in capsys.readouterr().out
