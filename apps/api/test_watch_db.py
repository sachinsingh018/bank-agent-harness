"""The watch endpoints and the trace backfill on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL: the records engine is injected with a FastAPI dependency override. Runs are
recorded through harness.records and removed again by tests_support.records_db. No model calls.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from apps.api.deps import get_records_engine
from apps.api.main import app
from evals.backfill_traces import backfill_dir
from harness.db.records_models import CaseEvent, RunTraceRow
from harness.models.config import get_profile
from harness.models.types import Message, TextBlock, ToolCallBlock, ToolResultBlock, Usage
from harness.prompts import load_prompt
from harness.records import record_case_result, record_eval_run, verify_chain
from harness.trace import CaseResult, RunTrace
from harness.verifiers.base import VerifierResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.records_db import records_cleaned


@pytest.fixture()
def client(harness_engine: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_records_engine] = lambda: harness_engine
    try:
        with records_cleaned(harness_engine):
            yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _alert(alert_id: str) -> dict[str, Any]:
    anchor = {"bank": 1, "acct": "A"}
    items = [
        {
            "txn_id": 100 + i,
            "ts": "2022-09-01T10:00:00",
            "from_account": anchor,
            "to_account": {"bank": 2, "acct": f"B{i}"},
            "amount_paid": {"value": "5.000000", "currency": "Euro"},
            "amount_received": {"value": "5.000000", "currency": "Euro"},
            "format": "ACH",
            "role": "out",
        }
        for i in range(3)
    ]
    return {
        "alert_id": alert_id,
        "detector": "fan-out",
        "anchor": anchor,
        "summary": "Account 1/A sent to 3 different accounts in 3 transfers.",
        "txn_count": 3,
        "account_count": 4,
        "transfers": {"items": items, "total_count": 3, "returned": 3, "truncated": False},
    }


def _result(alert_id: str) -> CaseResult:
    now = datetime.now(UTC)
    findings = {
        "alert_id": alert_id,
        "recommendation": "close",
        "confidence": 0.2,
        "typology": "NONE",
        "narrative": "Ordinary payouts.",
        "claims": [{"statement": "Three payouts.", "txn_ids": [100, 101, 102], "amounts": []}],
        "figures": [],
    }
    trace = RunTrace(
        case_id=alert_id,
        module="aml",
        started_at=now,
        finished_at=now,
        messages=[
            Message(role="user", blocks=[TextBlock(text="Investigate.")]),
            Message(
                role="assistant",
                blocks=[ToolCallBlock(id="c1", name="get_alert", arguments={"alert_id": alert_id})],
            ),
            Message(
                role="user",
                blocks=[ToolResultBlock(tool_call_id="c1", content=json.dumps(_alert(alert_id)))],
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
        totals=Usage(input_tokens=10, output_tokens=5),
        cost_usd="0",
        tool_steps=1,
        wall_ms=10,
        trace=trace,
    )


def _alert_id() -> str:
    return "AL-" + uuid.uuid4().hex[:12]


def _record(engine: Engine, result: CaseResult, **kwargs: Any) -> str:
    with engine.connect() as conn:
        return record_case_result(
            conn,
            result,
            profile=get_profile("scripted"),
            prompt=load_prompt(AML_SPEC.prompt),
            **kwargs,
        )


def test_showcase_lists_only_runs_with_stored_steps(
    client: TestClient, harness_engine: Engine
) -> None:
    traced = _record(harness_engine, _result(_alert_id()))
    untraced = _record(harness_engine, _result(_alert_id()), store_trace=False)
    response = client.get("/showcase")
    assert response.status_code == 200
    items = {i["run_id"]: i for i in response.json()["items"]}
    assert traced in items and untraced not in items
    item = items[traced]
    assert item["alert"]["detector"] == "fan-out"
    assert item["alert"]["detector_label"]
    assert len(item["graph"]["nodes"]) == 4 and len(item["graph"]["edges"]) == 3
    assert item["teaser"] == (
        "1 tool call, then recommended closure. An analyst makes the final decision."
    )


def test_steps_endpoint_replays_a_run(client: TestClient, harness_engine: Engine) -> None:
    traced = _record(harness_engine, _result(_alert_id()))
    untraced = _record(harness_engine, _result(_alert_id()), store_trace=False)
    response = client.get(f"/runs/{traced}/steps")
    assert response.status_code == 200
    body = response.json()
    kinds = [s["kind"] for s in body["steps"]]
    assert kinds == ["alert", "tool_call", "tool_result", "tool_call", "checks", "outcome"]
    assert body["steps"][-1]["txn_ids"] == [100, 101, 102]
    assert {n["id"] for n in body["graph"]["nodes"]} == {"1/A", "2/B0", "2/B1", "2/B2"}
    assert client.get(f"/runs/{untraced}/steps").status_code == 404
    assert client.get(f"/runs/{uuid.uuid4()}/steps").status_code == 404
    assert client.get("/runs/bad%20id/steps").status_code == 422


def test_backfill_adds_missing_traces_once(harness_engine: Engine) -> None:
    eval_run_id = f"test-{uuid.uuid4().hex[:12]}"
    manifest = {
        "run_id": eval_run_id,
        "dataset": {"name": "t", "version": "1", "sha256": "0" * 64},
        "git": {"commit": None, "dirty": None},
        "model": {"profile": "scripted", "provider": "scripted", "model": "scripted"},
        "prompt": {"version": "v1", "sha256": "0" * 64},
        "limits": {},
        "started_at": datetime.now(UTC).isoformat(),
    }
    first, second = _result(_alert_id()), _result(_alert_id())
    changed = first.model_copy(update={"status": "human_review", "reason": "limit_steps"})
    with records_cleaned(harness_engine):
        with harness_engine.begin() as conn:
            record_eval_run(conn, manifest)
        run_ids = [
            _record(harness_engine, r, eval_run_id=eval_run_id, store_trace=False)
            for r in (first, second)
        ]
        with harness_engine.begin() as conn:
            counts = backfill_dir(conn, eval_run_id, [changed, second, _result(_alert_id())])
        assert (counts.traced, counts.mismatched, counts.not_recorded) == (1, 1, 1)
        with harness_engine.begin() as conn:
            again = backfill_dir(conn, eval_run_id, [first, second])
        assert (again.traced, again.already) == (1, 1)
        with harness_engine.connect() as conn:
            stored = set(
                conn.execute(
                    select(RunTraceRow.run_id).where(RunTraceRow.run_id.in_(run_ids))
                ).scalars()
            )
            assert stored == set(run_ids)
            payloads = conn.execute(
                select(CaseEvent.payload).where(CaseEvent.run_id.in_(run_ids))
            ).scalars()
            assert all("trace_digest" not in p for p in payloads)
            assert verify_chain(conn, check_runs=True).ok
