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
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from apps.api.deps import (
    ReviewerSettings,
    get_records_engine,
    get_reviewer_settings,
    get_tool_engine,
)
from apps.api.main import app
from harness.db.models import AlertTransaction, Transaction
from harness.db.records_models import CaseEvent, Correction
from harness.models.config import get_profile
from harness.models.types import Usage
from harness.prompts import load_prompt
from harness.records import record_case_result, verify_chain
from harness.review import apply_review
from harness.trace import CaseResult, ModelCallRecord, ReviewOutcome, RunTrace
from harness.verifiers.base import VerifierResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.aml_db import local_url
from tests_support.records_db import records_cleaned
from tools.aml.db import build_tool_engine
from tools.aml.query_registry import Samples

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


# ------------------------------------------------------------ approve with edits


@dataclass(frozen=True)
class EditRun:
    run_id: str
    alert_id: str


def _alert_findings(alert_id: str, txns: list[Txn]) -> dict[str, Any]:
    """Findings that pass every AML verifier: a real alert, two of its transfers, an exact
    cited amount and a correct count."""
    ids = [t.txn_id for t in txns]
    return {
        "alert_id": alert_id,
        "recommendation": "escalate",
        "confidence": 0.8,
        "typology": "FAN-OUT",
        "narrative": "Agent narrative.",
        "claims": [
            {
                "statement": "Two transfers in the alert.",
                "txn_ids": ids,
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
        "figures": [{"kind": "count", "value": "2", "txn_ids": ids}],
    }


def _record(engine: Engine, case_id: str, findings: dict[str, Any]) -> str:
    result = CaseResult(
        case_id=case_id,
        status="proposed",
        reason=None,
        findings=findings,
        verifier_results=[VerifierResult(name="citations", passed=True, errors=[])],
        totals=Usage(input_tokens=120, output_tokens=30),
        cost_usd="0",
        tool_steps=2,
        wall_ms=40,
        trace=_trace(case_id),
    )
    with engine.connect() as conn:
        return record_case_result(
            conn, result, profile=get_profile("scripted"), prompt=load_prompt(AML_SPEC.prompt)
        )


@pytest.fixture()
def edit_runs(
    records_engine: Engine, harness_samples: Samples
) -> Iterator[tuple[EditRun, EditRun]]:
    """A run whose findings pass the verifiers, and one whose findings cite a missing txn.
    The alert comes from the rules, written under a test ruleset version (harness/conftest.py).
    """
    alert_id = harness_samples.alert_id
    with records_engine.connect() as conn:
        rows = conn.execute(
            select(
                Transaction.txn_id, Transaction.amt_paid, Transaction.cur_paid, Transaction.amt_recv
            )
            .join(AlertTransaction, AlertTransaction.txn_id == Transaction.txn_id)
            .where(AlertTransaction.alert_id == alert_id)
            .order_by(Transaction.txn_id)
            .limit(2)
        ).all()
    assert len(rows) == 2, "the test alert has fewer than two transfers"
    txns = [Txn(r.txn_id, r.amt_paid, r.cur_paid, r.amt_recv) for r in rows]
    good = _alert_findings(alert_id, txns)
    bad = _alert_findings(alert_id, txns)
    bad["claims"][0]["txn_ids"] = [*bad["claims"][0]["txn_ids"], MISSING_SMALL]
    with records_cleaned(records_engine):
        yield (
            EditRun(_record(records_engine, alert_id, good), alert_id),
            EditRun(_record(records_engine, alert_id, bad), alert_id),
        )


EDITS = {
    "recommendation": "close",
    "confidence": 0.3,
    "typology": "NONE",
    "narrative": "Regular payroll, not laundering.",
}


def _events(engine: Engine, run_id: str) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(CaseEvent.event).where(CaseEvent.run_id == run_id).order_by(CaseEvent.id)
            ).scalars()
        )


def _corrections(engine: Engine, run_id: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(Correction).where(Correction.run_id == run_id)).all())


def test_approve_with_edits_writes_an_edited_event_and_a_correction(
    client: TestClient, edit_runs: tuple[EditRun, EditRun], records_engine: Engine
) -> None:
    good, _ = edit_runs
    response = client.post(
        f"/runs/{good.run_id}/decision",
        json={
            "decision": "approve_with_edits",
            "note": "payroll pattern",
            "edits": EDITS,
            "correction": {
                "kind": "typology",
                "rule": "Regular payroll from one employer to many staff is not fan-out.",
            },
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event"] == "edited" and body["correction_id"] is not None
    with records_engine.connect() as conn:
        event = conn.execute(
            select(CaseEvent).where(CaseEvent.run_id == good.run_id, CaseEvent.event == "edited")
        ).one()
        assert event.payload["decision"] == "approve_with_edits"
        assert event.payload["note"] == "payroll pattern"
        assert event.payload["edited"] == EDITS
        assert event.payload["original"] == {
            "recommendation": "escalate",
            "confidence": 0.8,
            "typology": "FAN-OUT",
            "narrative": "Agent narrative.",
        }
        assert [v["passed"] for v in event.payload["verifier_results"]] == [True, True, True]
        report = verify_chain(conn, check_runs=True)
        assert report.ok and report.run_mismatch_ids == []
    saved = _corrections(records_engine, good.run_id)
    assert len(saved) == 1
    assert saved[0].decision_event_id == event.id and saved[0].id == body["correction_id"]
    assert (saved[0].module, saved[0].case_id, saved[0].kind) == ("aml", good.alert_id, "typology")
    assert saved[0].author == "human:test reviewer"

    assert good.run_id in _queue_ids(client, "decided")
    detail = client.get(f"/runs/{good.run_id}").json()
    assert detail["decision"]["decision"] == "approve_with_edits"
    again = client.post(
        f"/runs/{good.run_id}/decision", json={"decision": "approve"}, headers=HEADERS
    )
    assert again.status_code == 409

    listed = client.get("/corrections", params={"module": "aml", "limit": 100}).json()
    assert body["correction_id"] in [c["id"] for c in listed["items"]]
    assert client.get("/corrections", params={"module": "kyb"}).json()["items"] == []


def test_invalid_edits_are_refused_and_nothing_is_written(
    client: TestClient, edit_runs: tuple[EditRun, EditRun], records_engine: Engine
) -> None:
    good, bad = edit_runs
    cases = [
        (good.run_id, {**EDITS, "recommendation": "escalate", "confidence": 0.8}, "NONE"),
        (
            good.run_id,
            {**EDITS, "recommendation": "escalate", "typology": "FAN-OUT", "confidence": 0.4},
            "0.5",
        ),
        (
            bad.run_id,
            {**EDITS, "recommendation": "escalate", "typology": "FAN-OUT", "confidence": 0.9},
            "does not exist",
        ),
    ]
    for run_id, edits, expected in cases:
        response = client.post(
            f"/runs/{run_id}/decision",
            json={
                "decision": "approve_with_edits",
                "note": "fixing it",
                "edits": edits,
                "correction": {"kind": "other", "rule": "A lesson that is long enough."},
            },
            headers=HEADERS,
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert isinstance(detail, list) and any(expected in e for e in detail), detail
        assert _events(records_engine, run_id) == ["proposal_created"]
        assert _corrections(records_engine, run_id) == []

    no_note = client.post(
        f"/runs/{good.run_id}/decision",
        json={"decision": "approve_with_edits", "edits": EDITS},
        headers=HEADERS,
    )
    assert no_note.status_code == 422
    edits_with_approve = client.post(
        f"/runs/{good.run_id}/decision",
        json={"decision": "approve", "edits": EDITS},
        headers=HEADERS,
    )
    assert edits_with_approve.status_code == 422
    assert _events(records_engine, good.run_id) == ["proposal_created"]


def test_correction_with_a_date_is_refused(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    for rule in (
        "Ignore transfers made after 2022-09-11 in this data.",
        "Transfers in September are mostly noise here.",
        "Late transfers from 11/09/2022 are an artifact.",
    ):
        response = client.post(
            f"/runs/{runs.close}/decision",
            json={"decision": "approve", "correction": {"kind": "other", "rule": rule}},
            headers=HEADERS,
        )
        assert response.status_code == 422
        assert "decision 0006" in response.text
    assert _events(records_engine, runs.close) == ["proposal_created"]
    assert _corrections(records_engine, runs.close) == []

    ok = client.post(
        f"/runs/{runs.close}/decision",
        json={
            "decision": "approve",
            "correction": {"kind": "recommendation", "rule": "Small one-off transfers can close."},
        },
        headers=HEADERS,
    )
    assert ok.status_code == 200
    assert ok.json()["event"] == "approved"
    assert len(_corrections(records_engine, runs.close)) == 1


def test_corrections_list_pages_and_corrections_are_append_only(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    ids = []
    for run_id, decision, note in (
        (runs.escalate, "approve", None),
        (runs.review, "reject", "no evidence"),
    ):
        response = client.post(
            f"/runs/{run_id}/decision",
            json={
                "decision": decision,
                "note": note,
                "correction": {"kind": "evidence", "rule": f"Lesson for run {run_id[:8]}."},
            },
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["correction_id"])

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": 1, "module": "aml"}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/corrections", params=params).json()
        seen.extend(c["id"] for c in page["items"])
        cursor = page["next_cursor"]
        if set(ids) <= set(seen) or cursor is None:
            break
    assert seen[:2] == [ids[1], ids[0]]  # newest first
    assert client.get("/corrections", params={"limit": 101}).status_code == 422
    assert client.get("/corrections", params={"cursor": "bad!"}).status_code == 422
    assert client.get("/corrections", params={"module": "AML;"}).status_code == 422

    for statement in (
        "UPDATE corrections SET rule = 'changed rule text' WHERE id = :id",
        "DELETE FROM corrections WHERE id = :id",
        "TRUNCATE corrections",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            with records_engine.begin() as conn:
                conn.execute(text(statement), {"id": ids[0]})


def _rejected_by_checker(case_id: str, txns: list[Txn]) -> CaseResult:
    review = ReviewOutcome(
        verdict="reject",
        failure_reason=None,
        review={
            "verdict": "reject",
            "reasons": ["The ordinary explanation is not ruled out."],
            "concerns": [{"claim_index": 0, "issue": "Overstated."}],
            "confidence": 0.7,
        },
        verifier_results=[VerifierResult(name="review_claim_index", passed=True, errors=[])],
        profile="scripted",
        provider="scripted",
        model="scripted",
        prompt_version="v1",
        prompt_sha256="1" * 64,
        totals=Usage(input_tokens=40, output_tokens=4),
        cost_usd="0",
        tool_steps=0,
        wall_ms=10,
        trace=_trace(case_id),
    )
    return apply_review(_proposed(case_id, txns, "escalate"), review, required=True)


def test_detail_and_queue_show_the_checker_review(
    client: TestClient, runs: Runs, records_engine: Engine
) -> None:
    with records_engine.connect() as conn:
        run_id = record_case_result(
            conn,
            _rejected_by_checker(f"T-{uuid.uuid4()}", runs.txns),
            profile=get_profile("scripted"),
            prompt=load_prompt(AML_SPEC.prompt),
        )
    body = client.get(f"/runs/{run_id}").json()
    assert (body["status"], body["reason"]) == ("human_review", "reviewer_rejected")
    review = body["review"]
    assert review["verdict"] == "reject" and review["failure_reason"] is None
    assert review["review"]["concerns"] == [{"claim_index": 0, "issue": "Overstated."}]
    assert review["usage"]["input_tokens"] == 40 and review["prompt"]["version"] == "v1"
    assert review["model"]["profile"] == "scripted"
    assert client.get(f"/runs/{runs.escalate}").json()["review"] is None

    items = client.get("/queue", params={"limit": 100}).json()["items"]
    by_id = {i["run_id"]: i for i in items}
    assert by_id[run_id]["review_verdict"] == "reject"
    assert by_id[runs.escalate]["review_verdict"] is None
