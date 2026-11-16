"""The approval queue API on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL: the engines are injected with FastAPI dependency overrides. Runs are recorded
through harness.records.record_case_result and removed again by tests_support.records_db.
No model API calls."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select

from apps.api.deps import (
    ReviewerSettings,
    get_records_engine,
    get_reviewer_settings,
    get_tool_engine,
)
from apps.api.main import app
from harness.db.models import Transaction
from harness.db.records_models import CaseEvent
from harness.models.config import get_profile
from harness.models.types import Usage
from harness.prompts import load_prompt
from harness.records import record_case_result, verify_chain
from harness.trace import CaseResult, ModelCallRecord, RunTrace
from harness.verifiers.base import VerifierResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.aml_db import local_url
from tests_support.records_db import records_cleaned
from tools.aml.db import build_tool_engine

TOKEN = "test-reviewer-token"
HEADERS = {"X-Reviewer-Token": TOKEN}
MISSING_TXN = 9_000_000_000  # beyond the integer column
MISSING_SMALL = 2_000_000_000  # in range but not in the slice
LABEL_WORDS = ("is_laundering", "attempt_type", "attempt_id", "transaction_labels")


@dataclass(frozen=True)
class Txn:
    txn_id: int
    amt_paid: Decimal
    cur_paid: str
    amt_recv: Decimal


@dataclass(frozen=True)
class Runs:
    escalate: str
    close: str
    review: str
    txns: list[Txn]


@pytest.fixture(scope="module")
def records_engine() -> Iterator[Engine]:
    engine = create_engine(local_url(), echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def tool_engine() -> Iterator[Engine]:
    engine = build_tool_engine(local_url())
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def client(records_engine: Engine, tool_engine: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_records_engine] = lambda: records_engine
    app.dependency_overrides[get_tool_engine] = lambda: tool_engine
    app.dependency_overrides[get_reviewer_settings] = lambda: ReviewerSettings(
        token=TOKEN, name="test reviewer"
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _trace(case_id: str) -> RunTrace:
    now = datetime.now(UTC)
    return RunTrace(
        case_id=case_id,
        module="aml",
        started_at=now,
        finished_at=now,
        model_calls=[
            ModelCallRecord(
                index=0,
                provider="scripted",
                model="scripted",
                prompt_version="v3",
                prompt_sha256="0" * 64,
                usage=Usage(input_tokens=120, output_tokens=30),
                cost_usd="0",
                latency_ms=5,
                stop_reason="tool_use",
                request_id=None,
            )
        ],
    )


def _proposed(case_id: str, txns: list[Txn], recommendation: str) -> CaseResult:
    escalate = recommendation == "escalate"
    findings: dict[str, Any] = {
        "alert_id": case_id,
        "recommendation": recommendation,
        "confidence": 0.8 if escalate else 0.2,
        "typology": "FAN-OUT" if escalate else "NONE",
        "narrative": "Test narrative.",
        "claims": [
            {
                "statement": "Two transfers out.",
                "txn_ids": [txns[0].txn_id, txns[1].txn_id],
                "amounts": [
                    {
                        "txn_id": txns[0].txn_id,
                        "side": "paid",
                        "value": format(txns[0].amt_paid, "f"),
                        "currency": txns[0].cur_paid,
                    }
                ],
            }
        ],
        "figures": [
            {"kind": "count", "value": "2", "txn_ids": [txns[1].txn_id, MISSING_TXN, MISSING_SMALL]}
        ],
    }
    return CaseResult(
        case_id=case_id,
        status="proposed",
        reason=None,
        findings=findings,
        verifier_results=[VerifierResult(name="schema", passed=True, errors=[])],
        totals=Usage(input_tokens=120, output_tokens=30),
        cost_usd="0",
        tool_steps=2,
        wall_ms=40,
        trace=_trace(case_id),
    )


def _review(case_id: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status="human_review",
        reason="limit_steps",
        findings=None,
        verifier_results=[],
        totals=Usage(input_tokens=10, output_tokens=1),
        cost_usd="0",
        tool_steps=12,
        wall_ms=90,
        trace=_trace(case_id),
    )


@pytest.fixture()
def runs(records_engine: Engine) -> Iterator[Runs]:
    with records_engine.connect() as conn:
        rows = conn.execute(
            select(
                Transaction.txn_id, Transaction.amt_paid, Transaction.cur_paid, Transaction.amt_recv
            )
            .order_by(Transaction.txn_id)
            .limit(2)
        ).all()
    assert len(rows) == 2, "the test database has no transfers"
    txns = [Txn(r.txn_id, r.amt_paid, r.cur_paid, r.amt_recv) for r in rows]
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with records_cleaned(records_engine):
        with records_engine.connect() as conn:
            ids = [
                record_case_result(conn, result, profile=profile, prompt=prompt)
                for result in (
                    _proposed(f"T-{uuid.uuid4()}", txns, "escalate"),
                    _proposed(f"T-{uuid.uuid4()}", txns, "close"),
                    _review(f"T-{uuid.uuid4()}"),
                )
            ]
        yield Runs(escalate=ids[0], close=ids[1], review=ids[2], txns=txns)


def _no_labels(text: str) -> None:
    for word in LABEL_WORDS:
        assert word not in text, word


def _queue_ids(client: TestClient, state: str) -> set[str]:
    response = client.get("/queue", params={"state": state, "limit": 100})
    assert response.status_code == 200
    _no_labels(response.text)
    return {item["run_id"] for item in response.json()["items"]}


def test_queue_lists_waiting_runs_with_paging(client: TestClient, runs: Runs) -> None:
    mine = {runs.escalate, runs.close, runs.review}
    assert mine <= _queue_ids(client, "waiting")
    assert mine <= _queue_ids(client, "all")
    assert not mine & _queue_ids(client, "decided")

    items = client.get("/queue", params={"limit": 100}).json()["items"]
    by_id = {i["run_id"]: i for i in items}
    esc = by_id[runs.escalate]
    assert (esc["status"], esc["recommendation"], esc["typology"]) == (
        "proposed",
        "escalate",
        "FAN-OUT",
    )
    assert esc["confidence"] == 0.8 and esc["decision"] is None
    review = by_id[runs.review]
    assert (review["status"], review["reason"], review["recommendation"]) == (
        "human_review",
        "limit_steps",
        None,
    )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/queue", params=params).json()
        seen.extend(i["run_id"] for i in page["items"])
        cursor = page["next_cursor"]
        if mine <= set(seen) or cursor is None:
            break
    assert mine <= set(seen) and len(seen) == len(set(seen))
    assert client.get("/queue", params={"cursor": "bad!"}).status_code == 422
    assert client.get("/queue", params={"state": "other"}).status_code == 422


def test_detail_has_findings_transfers_and_events(client: TestClient, runs: Runs) -> None:
    response = client.get(f"/runs/{runs.escalate}")
    assert response.status_code == 200
    _no_labels(response.text)
    body = response.json()
    assert body["findings"]["recommendation"] == "escalate"
    assert body["verifier_results"] == [{"name": "schema", "passed": True, "errors": []}]
    assert body["totals"]["input_tokens"] == 120
    assert body["model"] == {"profile": "scripted", "provider": "scripted", "model": "scripted"}
    assert body["prompt"]["version"] == load_prompt(AML_SPEC.prompt).version
    assert [c["stop_reason"] for c in body["model_calls"]] == ["tool_use"]
    assert body["tool_calls"] is None
    assert [(e["event"], e["actor"]) for e in body["events"]] == [
        ("proposal_created", "agent-runner")
    ]
    assert body["decision"] is None
    transfers = {t["txn_id"]: t for t in body["cited_transfers"]}
    assert set(transfers) == {t.txn_id for t in runs.txns}
    for txn in runs.txns:
        got = transfers[txn.txn_id]
        assert Decimal(got["amount_paid"]["value"]) == txn.amt_paid
        assert got["amount_paid"]["value"] == format(txn.amt_paid, "f")
        assert got["amount_paid"]["currency"] == txn.cur_paid
        assert got["amount_received"]["value"] == format(txn.amt_recv, "f")
    assert body["missing_txn_ids"] == [MISSING_SMALL, MISSING_TXN]

    review = client.get(f"/runs/{runs.review}").json()
    assert review["findings"] is None and review["cited_transfers"] == []
    assert review["reason"] == "limit_steps"


def test_unknown_run_is_404(client: TestClient) -> None:
    unknown = str(uuid.uuid4())
    assert client.get(f"/runs/{unknown}").status_code == 404
    response = client.post(
        f"/runs/{unknown}/decision", json={"decision": "approve"}, headers=HEADERS
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_approve_writes_one_event_and_chain_verifies(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    response = client.post(
        f"/runs/{runs.escalate}/decision",
        json={"decision": "approve", "note": "evidence is clear"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["event"], body["actor"]) == ("approved", "human:test reviewer")
    with records_engine.connect() as conn:
        events = conn.execute(
            select(CaseEvent)
            .where(CaseEvent.run_id == runs.escalate, CaseEvent.event == "approved")
            .order_by(CaseEvent.id)
        ).all()
        assert len(events) == 1
        assert events[0].payload == {"decision": "approve", "note": "evidence is clear"}
        assert events[0].hash == body["hash"]
        report = verify_chain(conn, check_runs=True)
        assert report.ok and report.run_mismatch_ids == []

    assert runs.escalate in _queue_ids(client, "decided")
    assert runs.escalate not in _queue_ids(client, "waiting")
    detail = client.get(f"/runs/{runs.escalate}").json()
    assert detail["decision"]["decision"] == "approve"
    assert detail["decision"]["note"] == "evidence is clear"
    assert [e["event"] for e in detail["events"]] == ["proposal_created", "approved"]

    again = client.post(
        f"/runs/{runs.escalate}/decision",
        json={"decision": "reject", "note": "changed my mind"},
        headers=HEADERS,
    )
    assert again.status_code == 409
    with records_engine.connect() as conn:
        count = conn.execute(
            select(func.count()).where(
                CaseEvent.run_id == runs.escalate, CaseEvent.event.in_(("approved", "rejected"))
            )
        ).scalar_one()
        assert count == 1


def test_reject_needs_a_note_then_writes(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    for body in ({"decision": "reject"}, {"decision": "reject", "note": "  "}):
        response = client.post(f"/runs/{runs.review}/decision", json=body, headers=HEADERS)
        assert response.status_code == 422
    response = client.post(
        f"/runs/{runs.review}/decision",
        json={"decision": "reject", "note": "a normal payroll pattern"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["event"] == "rejected"
    with records_engine.connect() as conn:
        assert verify_chain(conn, check_runs=True).ok


def test_token_rules(client: TestClient, runs: Runs, records_engine: Engine) -> None:
    url = f"/runs/{runs.close}/decision"
    body = {"decision": "approve"}
    missing = client.post(url, json=body)
    assert missing.status_code == 403
    wrong = client.post(url, json=body, headers={"X-Reviewer-Token": "nope"})
    assert wrong.status_code == 403
    app.dependency_overrides[get_reviewer_settings] = lambda: ReviewerSettings(
        token=None, name=None
    )
    disabled = client.post(url, json=body, headers=HEADERS)
    assert disabled.status_code == 403
    assert disabled.json() == {"detail": "decisions are disabled on this server"}
    for response in (missing, wrong, disabled):
        assert TOKEN not in response.text
    assert runs.close in _queue_ids(client, "waiting")
    with records_engine.connect() as conn:
        count = conn.execute(
            select(func.count()).where(CaseEvent.run_id == runs.close)
        ).scalar_one()
        assert count == 1


def test_cited_transfer_lookups_are_capped(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    base = _proposed(f"T-{uuid.uuid4()}", runs.txns, "escalate")
    fake = [MISSING_SMALL + i for i in range(105)]
    findings = dict(base.findings or {})
    findings["claims"] = [*findings["claims"], {"statement": "Many.", "txn_ids": fake}]
    result = base.model_copy(update={"findings": findings})
    with records_engine.connect() as conn:
        run_id = record_case_result(
            conn, result, profile=get_profile("scripted"), prompt=load_prompt(AML_SPEC.prompt)
        )
    body = client.get(f"/runs/{run_id}").json()
    assert {t["txn_id"] for t in body["cited_transfers"]} == {t.txn_id for t in runs.txns}
    assert len(body["cited_transfers"]) + len(body["missing_txn_ids"]) == 100
    assert body["not_fetched_txn_ids"] == sorted([*fake, MISSING_TXN])[98:]
    assert len(body["not_fetched_txn_ids"]) == 8
