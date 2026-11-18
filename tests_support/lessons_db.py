"""Test helper: save a correction the way the approval flow does (a run, a human decision event
and the correction in the decision's transaction). Tests only; callers wrap it in
tests_support.records_db.records_cleaned so the rows are removed again."""

from datetime import UTC, datetime

from sqlalchemy import Engine, select

from apps.api.corrections import CorrectionIn, insert_correction
from harness.db.records_models import CaseEvent
from harness.db.session import is_local_url
from harness.models.config import get_profile
from harness.models.types import Usage
from harness.prompts import load_prompt
from harness.records import append_case_event, record_case_result
from harness.trace import CaseResult, RunTrace
from modules.aml.agent.spec import AML_SPEC


def save_correction(engine: Engine, case_id: str, rule: str) -> int:
    """Return the new correction id. Refuses an engine whose URL is not local."""
    if not is_local_url(engine.url.render_as_string(hide_password=True)):
        raise RuntimeError("save_correction only runs against a local test database")
    now = datetime.now(UTC)
    result = CaseResult(
        case_id=case_id,
        status="human_review",
        reason="limit_steps",
        findings=None,
        verifier_results=[],
        totals=Usage(input_tokens=1, output_tokens=1),
        cost_usd="0",
        tool_steps=1,
        wall_ms=1,
        trace=RunTrace(case_id=case_id, module="aml", started_at=now, finished_at=now),
    )
    with engine.connect() as conn:
        run_id = record_case_result(
            conn, result, profile=get_profile("scripted"), prompt=load_prompt(AML_SPEC.prompt)
        )
    with engine.begin() as conn:
        event_hash = append_case_event(
            conn,
            actor="human:tester",
            event="rejected",
            case_id=case_id,
            run_id=run_id,
            payload={"decision": "reject", "note": "test"},
        )
        event_id = conn.execute(
            select(CaseEvent.id).where(CaseEvent.hash == event_hash)
        ).scalar_one()
        return insert_correction(
            conn,
            CorrectionIn(kind="other", rule=rule),
            module="aml",
            case_id=case_id,
            run_id=run_id,
            decision_event_id=int(event_id),
            author="human:tester",
        )
