"""The records tables and the write path on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Writes that are committed are removed again by tests_support.records_db; the
trigger and tamper tests roll everything back."""

import argparse
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from harness import records
from harness.db.records_models import AgentReview, AgentRun, CaseEvent, ModelCall, RunTraceRow
from harness.models.config import get_profile
from harness.models.types import Usage
from harness.prompts import load_prompt
from harness.records import (
    append_case_event,
    event_hash,
    record_case_result,
    verify_chain,
)
from harness.review import apply_review
from harness.run_traces import MAX_TOOL_RESULT_CHARS, StoredTrace
from harness.trace import CaseResult, ModelCallRecord, ReviewOutcome, RunTrace
from harness.verifiers.base import VerifierResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.records_db import records_cleaned

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLES = ("eval_runs", "eval_results", "agent_runs", "model_calls", "case_events")
APPEND_ONLY = ("case_events", "agent_runs", "model_calls")
ROW_TRIGGER = "case_events_no_update_delete"
TRIGGERS = {f"{t}_{kind}" for t in APPEND_ONLY for kind in ("no_update_delete", "no_truncate")}


@pytest.fixture()
def clean(harness_engine: Engine) -> Iterator[None]:
    with records_cleaned(harness_engine):
        yield


def _alembic(conn: Connection, *x_args: str) -> Config:
    cfg = Config(cmd_opts=argparse.Namespace(x=list(x_args)))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.attributes["connection"] = conn
    return cfg


def _tables(conn: Connection) -> set[str]:
    rows = conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
    ).scalars()
    return set(rows)


def _triggers(conn: Connection) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
            "('case_events'::regclass, 'agent_runs'::regclass, 'model_calls'::regclass) "
            "AND NOT tgisinternal"
        )
    ).scalars()
    return set(rows)


def test_migration_down_and_up_again(harness_engine: Engine) -> None:
    """Runs inside one transaction that is rolled back, so the test database is unchanged.
    A non-empty audit log blocks the downgrade unless -x allow_audit_drop=1 is given."""
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            assert set(TABLES) <= _tables(conn)
            assert _triggers(conn) == TRIGGERS
            _append(conn, f"T-{uuid.uuid4()}")
            with pytest.raises(RuntimeError, match="allow_audit_drop=1"):
                command.downgrade(_alembic(conn), "0002")
            assert set(TABLES) <= _tables(conn)
            command.downgrade(_alembic(conn, "allow_audit_drop=1"), "0002")
            assert not set(TABLES) & _tables(conn)
            func_count = conn.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'records_refuse_change'")
            ).scalar_one()
            assert func_count == 0
            command.upgrade(_alembic(conn), "head")
            assert set(TABLES) <= _tables(conn)
            assert "agent_reviews" in _tables(conn)  # 0005
            assert "run_traces" in _tables(conn)  # 0006
            assert _triggers(conn) == TRIGGERS
            # An empty audit log downgrades without the flag.
            command.downgrade(_alembic(conn), "0002")
            assert not set(TABLES) & _tables(conn)
            command.upgrade(_alembic(conn), "head")
            assert _triggers(conn) == TRIGGERS
        finally:
            trans.rollback()


def _append(conn: Connection, case_id: str, n: int = 1) -> list[str]:
    return [
        append_case_event(
            conn,
            actor="agent-runner",
            event="human_review_created",
            case_id=case_id,
            run_id=None,
            payload={"status": "human_review", "i": i, "amount": Decimal("1.50")},
        )
        for i in range(n)
    ]


def test_case_events_refuse_update_delete_and_truncate(harness_engine: Engine) -> None:
    case_id = f"T-{uuid.uuid4()}"
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            _append(conn, case_id, 3)
            for sql in (
                "UPDATE case_events SET payload = '{}'::jsonb WHERE case_id = :c",
                "DELETE FROM case_events WHERE case_id = :c",
                "TRUNCATE case_events CASCADE",  # corrections reference case_events
            ):
                with pytest.raises(DBAPIError, match="append-only"):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"c": case_id})
            count = conn.execute(
                select(func.count()).where(CaseEvent.case_id == case_id)
            ).scalar_one()
            assert count == 3
        finally:
            trans.rollback()


def test_case_events_checks(harness_engine: Engine) -> None:
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            for actor, event in (("someone", "approved"), ("human:", "approved"), ("human:a", "x")):
                with pytest.raises(DBAPIError):
                    with conn.begin_nested():
                        append_case_event(
                            conn, actor=actor, event=event, case_id="T", run_id=None, payload={}
                        )
            append_case_event(
                conn, actor="human:ann", event="approved", case_id="T", run_id=None, payload={}
            )
        finally:
            trans.rollback()


def test_chain_of_three_verifies_and_tampering_is_found(harness_engine: Engine) -> None:
    case_id = f"T-{uuid.uuid4()}"
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            before = verify_chain(conn)
            assert before.ok
            hashes = _append(conn, case_id, 3)
            rows = conn.execute(
                select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)
            ).all()
            assert [r.hash for r in rows] == hashes
            assert [r.prev_hash for r in rows[1:]] == hashes[:2]
            first = rows[0]
            assert first.hash == event_hash(
                first.prev_hash,
                first.at,
                first.actor,
                first.event,
                first.case_id,
                first.run_id,
                first.payload,
            )
            assert first.payload["amount"] == "1.50"
            report = verify_chain(conn)
            assert report.ok and report.rows == before.rows + 3 and report.first_bad_id is None

            middle = rows[1].id
            conn.execute(text(f"ALTER TABLE case_events DISABLE TRIGGER {ROW_TRIGGER}"))
            conn.execute(
                text("UPDATE case_events SET payload = '{\"i\": 99}'::jsonb WHERE id = :i"),
                {"i": middle},
            )
            conn.execute(text(f"ALTER TABLE case_events ENABLE TRIGGER {ROW_TRIGGER}"))
            broken = verify_chain(conn)
            assert not broken.ok and broken.first_bad_id == middle
        finally:
            trans.rollback()
    with harness_engine.connect() as conn:
        assert verify_chain(conn).ok


def test_appends_from_two_connections_take_turns(harness_engine: Engine, clean: None) -> None:
    case_id = f"T-{uuid.uuid4()}"
    second_hash: list[str] = []

    def second() -> None:
        with harness_engine.connect() as conn_b:
            with conn_b.begin():
                second_hash.extend(_append(conn_b, case_id))

    with harness_engine.connect() as conn_a:
        trans = conn_a.begin()
        first_hash = _append(conn_a, case_id)
        worker = threading.Thread(target=second)
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "the second append must wait for the advisory lock"
        trans.commit()
        worker.join(timeout=10)
    assert not worker.is_alive()
    with harness_engine.connect() as conn:
        prev = conn.execute(
            select(CaseEvent.prev_hash).where(CaseEvent.hash == second_hash[0])
        ).scalar_one()
        assert prev == first_hash[0]
        assert verify_chain(conn).ok


def test_concurrent_bursts_keep_a_valid_chain(harness_engine: Engine, clean: None) -> None:
    case_id = f"T-{uuid.uuid4()}"
    start = threading.Barrier(2)

    def burst() -> None:
        start.wait()
        for _ in range(5):
            with harness_engine.connect() as conn:
                _append(conn, case_id)

    workers = [threading.Thread(target=burst) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30)
    with harness_engine.connect() as conn:
        count = conn.execute(select(func.count()).where(CaseEvent.case_id == case_id)).scalar_one()
        assert count == 10
        assert verify_chain(conn).ok


def _trace(case_id: str, calls: int) -> RunTrace:
    now = datetime.now(UTC)
    return RunTrace(
        case_id=case_id,
        module="aml",
        started_at=now,
        finished_at=now,
        model_calls=[
            ModelCallRecord(
                index=i,
                provider="scripted",
                model="scripted",
                prompt_version="v1",
                prompt_sha256="0" * 64,
                usage=Usage(input_tokens=100 + i, output_tokens=10, cache_read_tokens=5),
                cost_usd="0.001234",
                latency_ms=7,
                stop_reason="tool_use" if i < calls - 1 else "end_turn",
                request_id=f"req-{i}",
            )
            for i in range(calls)
        ],
    )


def _proposed(case_id: str) -> CaseResult:
    findings: dict[str, Any] = {
        "alert_id": case_id,
        "recommendation": "escalate",
        "typology": "FAN_OUT",
        "confidence": 0.8,
        "narrative": "Test.",
        "claims": [],
    }
    return CaseResult(
        case_id=case_id,
        status="proposed",
        reason=None,
        findings=findings,
        verifier_results=[VerifierResult(name="schema", passed=True, errors=[])],
        totals=Usage(input_tokens=201, output_tokens=20, cache_read_tokens=10),
        cost_usd="0.002468",
        tool_steps=3,
        wall_ms=1500,
        trace=_trace(case_id, 2),
    )


def _human_review(case_id: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status="human_review",
        reason="limit_steps",
        findings=None,
        verifier_results=[],
        totals=Usage(input_tokens=100, output_tokens=10, cache_read_tokens=5),
        cost_usd="0.001234",
        tool_steps=12,
        wall_ms=900,
        trace=_trace(case_id, 1),
    )


def test_record_case_result_writes_run_calls_and_event(harness_engine: Engine, clean: None) -> None:
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    proposed = _proposed(f"T-{uuid.uuid4()}")
    review = _human_review(f"T-{uuid.uuid4()}")
    with harness_engine.connect() as conn:
        run_p = record_case_result(conn, proposed, profile=profile, prompt=prompt, trace_path="x")
        run_r = record_case_result(conn, review, profile=profile, prompt=prompt)
    with harness_engine.connect() as conn:
        p = conn.execute(select(AgentRun).where(AgentRun.run_id == run_p)).one()
        assert (p.case_id, p.module, p.status) == (proposed.case_id, "aml", "proposed")
        assert p.reason is None
        assert p.cost_usd == Decimal("0.002468") and p.tool_steps == 3 and p.wall_ms == 1500
        assert p.findings == proposed.findings and p.trace_path == "x"
        assert p.verifier_results == [{"name": "schema", "passed": True, "errors": []}]
        assert p.totals["input_tokens"] == 201
        assert (p.prompt_version, p.prompt_sha256) == (prompt.version, prompt.sha256)
        assert (p.profile, p.provider, p.model) == ("scripted", "scripted", "scripted")
        assert p.eval_run_id is None and p.created_at is not None
        calls = conn.execute(
            select(ModelCall).where(ModelCall.run_id == run_p).order_by(ModelCall.id)
        ).all()
        assert [(c.input_tokens, c.cache_read_tokens, c.stop_reason) for c in calls] == [
            (100, 5, "tool_use"),
            (101, 5, "end_turn"),
        ]
        assert calls[0].cost_usd == Decimal("0.001234") and calls[0].request_id == "req-0"

        r = conn.execute(select(AgentRun).where(AgentRun.run_id == run_r)).one()
        assert (r.status, r.reason, r.findings) == ("human_review", "limit_steps", None)

        events = {
            e.run_id: e
            for e in conn.execute(
                select(CaseEvent).where(CaseEvent.run_id.in_([run_p, run_r]))
            ).all()
        }
        assert events[run_p].event == "proposal_created"
        assert events[run_p].actor == "agent-runner"
        assert events[run_p].case_id == proposed.case_id
        assert events[run_p].payload == {
            "status": "proposed",
            "reason": None,
            "recommendation": "escalate",
            "typology": "FAN_OUT",
            "confidence": 0.8,
            "run_id": run_p,
            "run_digest": records.current_run_digest(conn, run_p),
            "trace_digest": records.current_trace_digest(conn, run_p),
        }
        assert events[run_r].event == "human_review_created"
        assert events[run_r].payload == {
            "status": "human_review",
            "reason": "limit_steps",
            "run_id": run_r,
            "run_digest": records.current_run_digest(conn, run_r),
            "trace_digest": records.current_trace_digest(conn, run_r),
        }
        report = verify_chain(conn, check_runs=True)
        assert report.ok and report.runs_checked >= 2 and report.run_mismatch_ids == []
        assert report.traces_checked >= 2


def test_record_case_result_is_one_transaction(
    harness_engine: Engine, clean: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("fail after the run and call rows")

    monkeypatch.setattr(records, "append_case_event", boom)
    result = _proposed(f"T-{uuid.uuid4()}")
    with harness_engine.connect() as conn:
        with pytest.raises(RuntimeError):
            record_case_result(
                conn, result, profile=get_profile("scripted"), prompt=load_prompt(AML_SPEC.prompt)
            )
    with harness_engine.connect() as conn:
        runs = conn.execute(select(AgentRun.run_id).where(AgentRun.case_id == result.case_id)).all()
        assert runs == []
        calls = conn.execute(
            select(func.count())
            .select_from(ModelCall)
            .join(AgentRun, AgentRun.run_id == ModelCall.run_id)
            .where(AgentRun.case_id == result.case_id)
        ).scalar_one()
        assert calls == 0
        events = conn.execute(
            select(func.count()).where(CaseEvent.case_id == result.case_id)
        ).scalar_one()
        assert events == 0


def test_run_tables_refuse_update_delete_and_truncate(harness_engine: Engine) -> None:
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            run_id = record_case_result(
                conn, _proposed(f"T-{uuid.uuid4()}"), profile=profile, prompt=prompt
            )
            for sql in (
                "UPDATE agent_runs SET findings = NULL WHERE run_id = :r",
                "DELETE FROM agent_runs WHERE run_id = :r",
                "TRUNCATE agent_runs CASCADE",
                "UPDATE model_calls SET input_tokens = 0 WHERE run_id = :r",
                "DELETE FROM model_calls WHERE run_id = :r",
                "TRUNCATE model_calls",
            ):
                with pytest.raises(DBAPIError, match="append-only"):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"r": run_id})
        finally:
            trans.rollback()


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE agent_runs SET findings = jsonb_set(findings, '{recommendation}', '\"close\"') "
        "WHERE run_id = :r",
        "UPDATE model_calls SET input_tokens = input_tokens + 1 WHERE run_id = :r",
        "DELETE FROM model_calls WHERE run_id = :r",
    ],
)
def test_run_digest_finds_edited_run_records(harness_engine: Engine, tamper: str) -> None:
    """Triggers disabled inside a rolled-back transaction, as an owner could do."""
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            run_id = record_case_result(
                conn, _proposed(f"T-{uuid.uuid4()}"), profile=profile, prompt=prompt
            )
            event_id = conn.execute(
                select(CaseEvent.id).where(CaseEvent.run_id == run_id)
            ).scalar_one()
            assert verify_chain(conn, check_runs=True).ok
            for table in ("agent_runs", "model_calls"):
                conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            conn.execute(text(tamper), {"r": run_id})
            for table in ("agent_runs", "model_calls"):
                conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
            assert verify_chain(conn).ok  # the chain itself is intact
            report = verify_chain(conn, check_runs=True)
            assert not report.ok and report.first_bad_id is None
            assert report.run_mismatch_ids == [event_id]
        finally:
            trans.rollback()


def _reviewed(case_id: str, verdict: str | None) -> CaseResult:
    review = ReviewOutcome(
        verdict=verdict,  # type: ignore[arg-type]
        failure_reason=None if verdict is not None else "limit_steps",
        review=None
        if verdict is None
        else {"verdict": verdict, "reasons": ["Checked."], "concerns": [], "confidence": 0.6},
        verifier_results=[VerifierResult(name="review_claim_index", passed=True, errors=[])],
        profile="scripted",
        provider="scripted",
        model="scripted",
        prompt_version="v1",
        prompt_sha256="1" * 64,
        totals=Usage(input_tokens=50, output_tokens=5),
        cost_usd="0.0001234",
        tool_steps=1,
        wall_ms=300,
        trace=_trace(case_id, 1),
    )
    return apply_review(_proposed(case_id), review, required=True)


def test_reviewed_runs_write_a_review_row_and_digest(harness_engine: Engine, clean: None) -> None:
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    approved = _reviewed(f"T-{uuid.uuid4()}", "approve")
    rejected = _reviewed(f"T-{uuid.uuid4()}", "reject")
    failed = _reviewed(f"T-{uuid.uuid4()}", None)
    with harness_engine.connect() as conn:
        ids = [
            record_case_result(conn, r, profile=profile, prompt=prompt)
            for r in (approved, rejected, failed)
        ]
    with harness_engine.connect() as conn:
        rows = {
            r.run_id: r
            for r in conn.execute(select(AgentReview).where(AgentReview.run_id.in_(ids))).all()
        }
        assert [rows[i].verdict for i in ids] == ["approve", "reject", None]
        assert rows[ids[2]].failure_reason == "limit_steps" and rows[ids[2]].review is None
        assert rows[ids[0]].cost_usd == Decimal("0.000123") and rows[ids[0]].tool_steps == 1
        assert rows[ids[0]].usage["input_tokens"] == 50
        runs = {r.run_id: r for r in conn.execute(select(AgentRun).where(AgentRun.run_id.in_(ids)))}
        assert [runs[i].status for i in ids] == ["proposed", "human_review", "human_review"]
        assert runs[ids[1]].reason == "reviewer_rejected"
        assert runs[ids[0]].totals["input_tokens"] == 251  # maker plus checker
        events = {
            e.run_id: e for e in conn.execute(select(CaseEvent).where(CaseEvent.run_id.in_(ids)))
        }
        assert events[ids[0]].event == "proposal_created"
        assert events[ids[1]].event == "human_review_created"
        for run_id in ids:
            payload = events[run_id].payload
            assert payload["review_digest"] == records.current_review_digest(conn, run_id)
            assert payload["run_digest"] == records.current_run_digest(conn, run_id)
        assert events[ids[2]].payload["review_verdict"] is None
        report = verify_chain(conn, check_runs=True)
        assert report.ok and report.reviews_checked >= 3


def test_review_rows_are_append_only_and_tampering_is_found(harness_engine: Engine) -> None:
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            run_id = record_case_result(
                conn, _reviewed(f"T-{uuid.uuid4()}", "approve"), profile=profile, prompt=prompt
            )
            for sql in (
                "UPDATE agent_reviews SET verdict = 'reject' WHERE run_id = :r",
                "DELETE FROM agent_reviews WHERE run_id = :r",
                "TRUNCATE agent_reviews",
            ):
                with pytest.raises(DBAPIError, match="append-only"):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"r": run_id})
            event_id = conn.execute(
                select(CaseEvent.id).where(CaseEvent.run_id == run_id)
            ).scalar_one()
            assert verify_chain(conn, check_runs=True).ok
            conn.execute(text("ALTER TABLE agent_reviews DISABLE TRIGGER USER"))
            conn.execute(
                text("UPDATE agent_reviews SET verdict = 'reject' WHERE run_id = :r"),
                {"r": run_id},
            )
            conn.execute(text("ALTER TABLE agent_reviews ENABLE TRIGGER USER"))
            assert verify_chain(conn).ok  # the chain itself is intact
            report = verify_chain(conn, check_runs=True)
            assert not report.ok and report.run_mismatch_ids == [event_id]
        finally:
            trans.rollback()


def test_a_run_recorded_before_reviews_still_verifies(harness_engine: Engine) -> None:
    """Rows and event written exactly as the recorder did before maker-checker: no
    agent_reviews row and no review_digest in the payload. The run digest must not change."""
    run_id = str(uuid.uuid4())
    case_id = f"T-{uuid.uuid4()}"
    run: dict[str, Any] = {
        "run_id": run_id,
        "case_id": case_id,
        "module": "aml",
        "status": "human_review",
        "reason": "limit_steps",
        "findings": None,
        "verifier_results": [],
        "totals": {"input_tokens": 1, "output_tokens": 1},
        "cost_usd": Decimal("0.000001"),
        "tool_steps": 12,
        "wall_ms": 5,
        "prompt_version": "v2",
        "prompt_sha256": "2" * 64,
        "profile": "scripted",
        "provider": "scripted",
        "model": "scripted",
        "eval_run_id": None,
        "trace_path": None,
        "created_at": datetime(2026, 11, 16, 1, 2, 3, 456789, tzinfo=UTC),
    }
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(insert(AgentRun).values(**run))
            payload = {
                "status": "human_review",
                "reason": "limit_steps",
                "run_id": run_id,
                "run_digest": records.run_digest(run, []),
            }
            append_case_event(
                conn,
                actor="agent-runner",
                event="human_review_created",
                case_id=case_id,
                run_id=run_id,
                payload=payload,
            )
            assert records.current_run_digest(conn, run_id) == payload["run_digest"]
            assert records.current_review_digest(conn, run_id) is None
            report = verify_chain(conn, check_runs=True)
            assert report.ok and report.run_mismatch_ids == []
        finally:
            trans.rollback()


def test_run_traces_are_stored_append_only_and_tampering_is_found(harness_engine: Engine) -> None:
    """Each recorded run gets one run_traces row with the capped maker and checker traces;
    its event carries trace_digest, and an edited trace row shows up in verify."""
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            run_id = record_case_result(
                conn, _reviewed(f"T-{uuid.uuid4()}", "approve"), profile=profile, prompt=prompt
            )
            row = conn.execute(select(RunTraceRow).where(RunTraceRow.run_id == run_id)).one()
            stored = StoredTrace.model_validate(row.trace)
            assert stored.checker is not None and stored.maker.module == "aml"
            assert stored.tool_result_cap == MAX_TOOL_RESULT_CHARS
            for sql in (
                "UPDATE run_traces SET case_card = NULL WHERE run_id = :r",
                "DELETE FROM run_traces WHERE run_id = :r",
                "TRUNCATE run_traces",
            ):
                with pytest.raises(DBAPIError, match="append-only"):
                    with conn.begin_nested():
                        conn.execute(text(sql), {"r": run_id})
            event = conn.execute(select(CaseEvent).where(CaseEvent.run_id == run_id)).one()
            assert event.payload["trace_digest"] == records.current_trace_digest(conn, run_id)
            assert verify_chain(conn, check_runs=True).ok
            conn.execute(text("ALTER TABLE run_traces DISABLE TRIGGER USER"))
            conn.execute(
                text("UPDATE run_traces SET case_card = '{}'::jsonb WHERE run_id = :r"),
                {"r": run_id},
            )
            conn.execute(text("ALTER TABLE run_traces ENABLE TRIGGER USER"))
            assert verify_chain(conn).ok  # the chain itself is intact
            report = verify_chain(conn, check_runs=True)
            assert not report.ok and report.run_mismatch_ids == [event.id]
        finally:
            trans.rollback()


def test_a_run_recorded_without_its_trace_has_no_trace_digest(harness_engine: Engine) -> None:
    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with harness_engine.connect() as conn:
        trans = conn.begin()
        try:
            run_id = record_case_result(
                conn,
                _proposed(f"T-{uuid.uuid4()}"),
                profile=profile,
                prompt=prompt,
                store_trace=False,
            )
            event = conn.execute(select(CaseEvent).where(CaseEvent.run_id == run_id)).one()
            assert "trace_digest" not in event.payload
            assert records.current_trace_digest(conn, run_id) is None
            assert verify_chain(conn, check_runs=True).ok
        finally:
            trans.rollback()
