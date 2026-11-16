"""Test helper: remove the records a test wrote, so the local test database stays tidy.

case_events, agent_runs and model_calls refuse DELETE, so the cleanup turns their user
triggers off inside one transaction (allowed for the table owner, the local postgres test
role), deletes only the rows added after the snapshot (the tail of the chain, so the chain
stays valid) and turns the triggers back on. Tests only: it refuses any engine whose URL is
not local, and a static test keeps non-test code from importing tests_support.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, delete, func, select, text

from harness.db.records_models import AgentRun, CaseEvent, EvalResult, EvalRun
from harness.db.session import is_local_url

APPEND_ONLY_TABLES = ("case_events", "agent_runs", "model_calls")


@dataclass(frozen=True)
class RecordsSnapshot:
    max_event_id: int
    run_ids: frozenset[str]
    eval_run_ids: frozenset[str]


def snapshot(engine: Engine) -> RecordsSnapshot:
    with engine.connect() as conn:
        max_id = conn.execute(select(func.coalesce(func.max(CaseEvent.id), 0))).scalar_one()
        runs = conn.execute(select(AgentRun.run_id)).scalars().all()
        evals = conn.execute(select(EvalRun.eval_run_id)).scalars().all()
    return RecordsSnapshot(int(max_id), frozenset(runs), frozenset(evals))


def remove_since(engine: Engine, snap: RecordsSnapshot) -> None:
    if not is_local_url(engine.url.render_as_string(hide_password=True)):
        raise RuntimeError("remove_since only runs against a local test database")
    with engine.begin() as conn:
        for table in APPEND_ONLY_TABLES:
            conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
        conn.execute(delete(CaseEvent).where(CaseEvent.id > snap.max_event_id))
        conn.execute(delete(AgentRun).where(AgentRun.run_id.not_in(snap.run_ids)))
        for table in APPEND_ONLY_TABLES:
            conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
        conn.execute(delete(EvalResult).where(EvalResult.eval_run_id.not_in(snap.eval_run_ids)))
        conn.execute(delete(EvalRun).where(EvalRun.eval_run_id.not_in(snap.eval_run_ids)))


@contextmanager
def records_cleaned(engine: Engine) -> Iterator[RecordsSnapshot]:
    snap = snapshot(engine)
    try:
        yield snap
    finally:
        remove_since(engine, snap)
