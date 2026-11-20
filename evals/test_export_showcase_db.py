"""The showcase exporter on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Runs are recorded through harness.records and removed again by
tests_support.records_db; the fixture alerts live under their own ruleset version and are
deleted after. The results.jsonl, dataset, score and baseline files are temporary. No model calls.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, insert, select

from evals import export_showcase as ex
from evals.test_export_showcase import _write_score
from harness.db.models import Alert, Transaction
from harness.models.config import get_profile
from harness.models.types import Message, TextBlock, ToolCallBlock, ToolResultBlock, Usage
from harness.prompts import load_prompt
from harness.records import record_case_result
from harness.trace import CaseResult, ModelCallRecord, RunTrace, ToolCallRecord
from harness.verifiers.base import VerifierResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.records_db import records_cleaned

RULESET = "test-export-showcase"
NOW = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)


def _ref(bank: int, acct: str) -> dict[str, Any]:
    return {"bank": bank, "acct": acct}


def _real_transfers(engine: Engine) -> list[dict[str, Any]]:
    """Three real transfers between different accounts, as tool result items."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(Transaction.__table__)
            .where(Transaction.from_acct != Transaction.to_acct)
            .order_by(Transaction.txn_id)
            .limit(3)
        ).all()
    assert len(rows) == 3, "the test database has no transactions loaded"
    return [
        {
            "txn_id": r.txn_id,
            "ts": r.ts.isoformat(),
            "from_account": _ref(r.from_bank, r.from_acct),
            "to_account": _ref(r.to_bank, r.to_acct),
            "amount_paid": {"value": "1.00", "currency": r.cur_paid},
            "amount_received": {"value": "1.00", "currency": r.cur_recv},
            "format": r.fmt,
            "role": "out",
        }
        for r in rows
    ]


def _result(alert_id: str, items: list[dict[str, Any]]) -> CaseResult:
    alert = {
        "alert_id": alert_id,
        "detector": "fan-out",
        "anchor": items[0]["from_account"],
        "summary": "A long summary. " * 200,  # makes the raw result longer than the cap
        "txn_count": len(items),
        "account_count": 4,
        "transfers": {"items": items, "total_count": 3, "returned": 3, "truncated": False},
    }
    txn_ids = [i["txn_id"] for i in items]
    findings = {
        "alert_id": alert_id,
        "recommendation": "close",
        "confidence": 0.2,
        "typology": "NONE",
        "narrative": "Ordinary payouts.",
        "claims": [{"statement": "Three payouts.", "txn_ids": txn_ids, "amounts": []}],
        "figures": [],
    }
    calls = [
        ModelCallRecord(
            index=i,
            provider="openrouter",
            model="free/model",
            prompt_version="v1",
            prompt_sha256="0" * 64,
            usage=Usage(input_tokens=100 + i, output_tokens=10 + i),
            cost_usd="0",
            latency_ms=1000 + i,
            stop_reason="tool_use",
            request_id=f"req-{i}",
        )
        for i in range(2)
    ]
    plan: list[tuple[str, int | None, dict[str, Any]]] = [
        ("get_alert", 1, {"alert_id": alert_id}),
        ("submit_findings", None, findings),
    ]
    tools = [
        ToolCallRecord(
            index=i,
            step=step,
            name=name,
            arguments=args,
            ok=True,
            error_code=None,
            duration_ms=7 + i,
            size=10,
            truncated=False,
            repeat=False,
            content_sha256="0" * 64,
        )
        for i, (name, step, args) in enumerate(plan)
    ]
    trace = RunTrace(
        case_id=alert_id,
        module="aml",
        started_at=NOW,
        finished_at=NOW,
        model_calls=calls,
        tool_calls=tools,
        messages=[
            Message(role="user", blocks=[TextBlock(text="Investigate.")]),
            Message(
                role="assistant",
                blocks=[ToolCallBlock(id="c1", name="get_alert", arguments={"alert_id": alert_id})],
            ),
            Message(
                role="user",
                blocks=[ToolResultBlock(tool_call_id="c1", content=json.dumps(alert))],
            ),
            Message(
                role="assistant",
                blocks=[ToolCallBlock(id="s1", name="submit_findings", arguments=findings)],
            ),
        ],
    )
    return CaseResult(
        case_id=alert_id,
        status="proposed",
        reason=None,
        findings=findings,
        verifier_results=[VerifierResult(name="citations", passed=True, errors=[])],
        totals=Usage(input_tokens=201, output_tokens=21),
        cost_usd="0",
        tool_steps=1,
        wall_ms=10,
        trace=trace,
    )


@dataclass(frozen=True)
class Fixture:
    url: str
    good: str
    scripted: str
    held: str
    no_file: str
    sources: ex.Sources
    txn_ids: list[int]


def _alert_row(alert_id: str) -> dict[str, Any]:
    return {
        "alert_id": alert_id,
        "ruleset_version": RULESET,
        "detector": "fan-out",
        "anchor_bank": 1,
        "anchor_acct": "A",
        "window_start": datetime(2022, 9, 1),
        "window_end": datetime(2022, 9, 2),
        "txn_count": 3,
        "account_count": 4,
        "params": {},
        "summary": "test alert",
    }


@pytest.fixture()
def fx(local_engine: Engine, tmp_path: Path) -> Iterator[Fixture]:
    items = _real_transfers(local_engine)
    ids = {k: "AL-" + uuid.uuid4().hex[:12] for k in ("good", "scripted", "held", "no_file")}
    results = tmp_path / "results.jsonl"
    cases = {k: _result(a, items) for k, a in ids.items()}
    with results.open("w", encoding="utf-8") as fh:
        for case in cases.values():
            record = {"case_id": case.case_id, "trace": case.trace.model_dump(mode="json")}
            fh.write(json.dumps(record) + "\n")
    dev = tmp_path / "dev.json"
    held = tmp_path / "held.json"
    dev.write_text(json.dumps({"alert_ids": [ids["good"], ids["scripted"], ids["no_file"]]}))
    held.write_text(json.dumps({"alert_ids": [ids["held"]]}))
    scores = _write_score(tmp_path)
    sources = ex.Sources(
        dev_dataset=dev,
        heldout_dataset=held,
        score=scores.score,
        heldout_score=scores.heldout_score,
        rules_baseline=scores.rules_baseline,
    )
    prompt = load_prompt(AML_SPEC.prompt)
    with local_engine.begin() as conn:
        conn.execute(insert(Alert), [_alert_row(a) for a in ids.values()])
    try:
        with records_cleaned(local_engine):
            run_ids: dict[str, str] = {}
            for key, case in cases.items():
                profile = get_profile("scripted" if key == "scripted" else "openrouter-free-dev")
                gone = tmp_path / "gone" / "results.jsonl"
                path = str(gone if key == "no_file" else results)
                with local_engine.connect() as conn:
                    run_ids[key] = record_case_result(
                        conn, case, profile=profile, prompt=prompt, trace_path=path
                    )
            yield Fixture(
                url=local_engine.url.render_as_string(hide_password=False),
                good=run_ids["good"],
                scripted=run_ids["scripted"],
                held=run_ids["held"],
                no_file=run_ids["no_file"],
                sources=sources,
                txn_ids=sorted(i["txn_id"] for i in items),
            )
    finally:
        with local_engine.begin() as conn:
            conn.execute(delete(Alert).where(Alert.ruleset_version == RULESET))


def _export(fx: Fixture, run_id: str, out: Path) -> dict[str, bytes]:
    curated = (ex.Curated(run_id, "Closed", "A test case.", None),)
    return ex.export(
        fx.url,
        out,
        export_date="2026-11-19",
        git_commit="abc1234",
        sources=fx.sources,
        curated=curated,
        stories=(),
        featured=run_id,
    )


def test_export_is_allowlisted_and_deterministic(fx: Fixture, tmp_path: Path) -> None:
    first = _export(fx, fx.good, tmp_path / "a")
    second = _export(fx, fx.good, tmp_path / "b")
    assert first == second
    for name, body in first.items():
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
        assert body.endswith(b"\n")
    assert set(first) == {"manifest.json", "showcase.json", "results.json", f"runs/{fx.good}.json"}

    run = json.loads(first[f"runs/{fx.good}.json"])
    assert set(run) == {"steps", "case", "provenance", "effort"}
    assert "trace_id" not in run["case"]
    assert {"run_id", "findings", "model_calls", "tool_calls", "events"} <= set(run["case"])
    for event in run["case"]["events"]:
        assert set(event["payload"]) <= ex.EVENT_PAYLOAD_KEYS
        assert "run_digest" not in event["payload"]
    capped = [s for s in run["steps"]["steps"] if s.get("raw_truncated")]
    assert capped and all(len(s["raw"]) == ex.RAW_CAP for s in capped)
    edges = run["steps"]["graph"]["edges"]
    assert sorted(t["txn_id"] for e in edges for t in e["transfers"]) == fx.txn_ids
    assert run["provenance"]["ruleset_version"] == RULESET
    assert run["provenance"]["prompt_version"] == run["case"]["prompt"]["version"]
    assert run["effort"]["per_step"] is not None
    assert [t["duration_ms"] for t in run["effort"]["tools"]] == [7, 8]
    assert b"req-0" not in first[f"runs/{fx.good}.json"]

    manifest = json.loads(first["manifest.json"])
    assert manifest["order"] == [fx.good] and manifest["featured"] == fx.good
    assert manifest["export_date"] == "2026-11-19" and manifest["git_commit"] == "abc1234"
    showcase = json.loads(first["showcase.json"])
    assert [i["run_id"] for i in showcase] == [fx.good]
    results = json.loads(first["results.json"])
    assert results["headline_source"] == "heldout"
    assert results["heldout"]["eval_run_id"] == "eval-held"
    assert manifest["eval_run_id"] == results["eval_run_id"]


def test_export_refuses_scripted_held_out_and_missing_file(fx: Fixture, tmp_path: Path) -> None:
    for run_id, words in (
        (fx.scripted, "scripted"),
        (fx.held, "held out"),
        (fx.no_file, "results.jsonl is missing"),
    ):
        with pytest.raises(ex.ExportError, match=words):
            _export(fx, run_id, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_export_refuses_non_local_url(tmp_path: Path) -> None:
    with pytest.raises(ex.ExportError, match="non-local"):
        ex.export(
            "postgresql+psycopg://u:p@db.example.com:5432/x",
            tmp_path,
            export_date="2026-11-19",
            git_commit="abc1234",
        )
