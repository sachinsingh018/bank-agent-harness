"""The approval queue: list runs waiting for a human, show one run, record a decision.

The agent proposes, a person decides (four-eyes). Reads of runs and events use the records
connection; cited transfers are read through the guarded tool door (get_transaction). The
only writes are one decision event (approved, rejected or edited) in the append-only audit
log, through harness.records.append_case_event, plus an optional correction in the same
transaction, and only when the server has a reviewer configured. Edited findings must pass the
module's output model and deterministic verifiers first (apps/api/edits.py).
Errors are generic and never carry database details.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Connection, Engine, func, select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from apps.api.corrections import CorrectionIn, insert_correction
from apps.api.deps import (
    ReviewerSettings,
    get_records_engine,
    get_reviewer_settings,
    get_tool_engine,
    valid_reviewer_name,
)
from apps.api.edits import FindingsEdits, check_edits
from harness.db.records_models import AgentRun, CaseEvent, Correction, ModelCall
from harness.db.session import DatabaseConnectionError
from harness.records import CASE_EVENTS_LOCK_KEY, append_case_event
from tools.aml.db import open_readonly
from tools.aml.models import TransactionInput, TransferRecord
from tools.aml.paging import ToolFailure
from tools.aml.queries import get_transaction

router = APIRouter()

DECISION_EVENTS = ("approved", "rejected", "edited")
Decision = Literal["approve", "reject", "approve_with_edits"]
DecisionEvent = Literal["approved", "rejected", "edited"]
EVENT_FOR: dict[Decision, DecisionEvent] = {
    "approve": "approved",
    "reject": "rejected",
    "approve_with_edits": "edited",
}
DECISION_FOR: dict[str, Decision] = {v: k for k, v in EVENT_FOR.items()}
MAX_NOTE = 2000
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
TRACE_FILE_NAME = "results.jsonl"
MAX_CITED_LOOKUPS = 100  # cited transfers fetched per run detail; the rest are listed
MAX_TXN_ID = 2_147_483_647  # transactions.txn_id is a 32 bit integer
DISABLED = "decisions are disabled on this server"
FORBIDDEN = "invalid reviewer token"
DB_ERROR = "database error"
RUN_NOT_FOUND = "run not found"

QueueState = Literal["waiting", "decided", "all"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- response models


class DecisionInfo(_Out):
    decision: Decision
    actor: str
    at: datetime
    note: str | None


class QueueItem(_Out):
    run_id: str
    case_id: str  # the alert id for the AML module
    module: str
    status: str
    reason: str | None
    recommendation: str | None
    confidence: float | None
    typology: str | None
    created_at: datetime
    decision: DecisionInfo | None


class QueuePage(_Out):
    state: QueueState
    items: list[QueueItem]
    next_cursor: str | None


class VerifierOut(_Out):
    name: str
    passed: bool
    errors: list[str]


class TotalsOut(_Out):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class ModelInfo(_Out):
    profile: str
    provider: str
    model: str


class PromptInfo(_Out):
    version: str
    sha256: str


class ModelCallOut(_Out):
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: str
    latency_ms: int
    stop_reason: str


class ToolCallOut(_Out):
    index: int
    step: int | None
    name: str
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None
    duration_ms: int
    size: int
    truncated: bool


class EventOut(_Out):
    event: str
    actor: str
    at: datetime
    payload: dict[str, Any]


class RunDetail(_Out):
    run_id: str
    case_id: str
    module: str
    status: str
    reason: str | None
    findings: dict[str, Any] | None
    verifier_results: list[VerifierOut]
    totals: TotalsOut
    cost_usd: str
    tool_steps: int
    wall_ms: int
    model: ModelInfo
    prompt: PromptInfo
    eval_run_id: str | None
    created_at: datetime
    model_calls: list[ModelCallOut]
    tool_calls: list[ToolCallOut] | None  # None when the trace file is not available
    events: list[EventOut]
    cited_transfers: list[TransferRecord]
    missing_txn_ids: list[int]
    not_fetched_txn_ids: list[int]  # cited, but over the lookup cap
    decision: DecisionInfo | None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    note: Annotated[str, Field(max_length=MAX_NOTE)] | None = None
    edits: FindingsEdits | None = None  # required for approve_with_edits, refused otherwise
    correction: CorrectionIn | None = None  # an optional lesson, with any decision

    @model_validator(mode="after")
    def _note_rules(self) -> Self:
        if self.note is not None:
            self.note = self.note.strip() or None
        if self.decision == "reject" and self.note is None:
            raise ValueError("a rejection needs a note")
        if self.decision == "approve_with_edits":
            if self.note is None:
                raise ValueError("an approval with edits needs a note")
            if self.edits is None:
                raise ValueError("an approval with edits needs the edited fields")
        elif self.edits is not None:
            raise ValueError("edits are only allowed with approve_with_edits")
        return self


class DecisionResponse(_Out):
    run_id: str
    case_id: str
    event: DecisionEvent
    actor: str
    hash: str
    correction_id: int | None


class CorrectionOut(_Out):
    id: int
    created_at: datetime
    module: str
    case_id: str
    run_id: str
    decision_event_id: int
    kind: str
    rule: str
    author: str


class CorrectionsPage(_Out):
    items: list[CorrectionOut]
    next_cursor: str | None


# ------------------------------------------------------------------------- helpers


def _safely[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (SQLAlchemyError, OSError, DatabaseConnectionError):
        raise HTTPException(status_code=503, detail=DB_ERROR) from None


def encode_cursor(created_at: datetime, run_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), run_id]).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        at, run_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        parsed = datetime.fromisoformat(at)
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise HTTPException(status_code=422, detail="invalid cursor") from None
    if parsed.tzinfo is None or not isinstance(run_id, str):
        raise HTTPException(status_code=422, detail="invalid cursor")
    return parsed, run_id


def _decision_info(
    event: str | None, actor: str | None, at: datetime | None, payload: Any
) -> DecisionInfo | None:
    if event is None or actor is None or at is None:
        return None
    note = payload.get("note") if isinstance(payload, dict) else None
    return DecisionInfo(
        decision=DECISION_FOR[event],
        actor=actor,
        at=at,
        note=note if isinstance(note, str) else None,
    )


def _first_decisions() -> Any:
    """The first decision event of each run (there is at most one, enforced on write)."""
    first = (
        select(CaseEvent.run_id, func.min(CaseEvent.id).label("event_id"))
        .where(CaseEvent.event.in_(DECISION_EVENTS), CaseEvent.run_id.is_not(None))
        .group_by(CaseEvent.run_id)
        .subquery()
    )
    return (
        select(
            CaseEvent.run_id,
            CaseEvent.event,
            CaseEvent.actor,
            CaseEvent.at,
            CaseEvent.payload,
        )
        .join(first, first.c.event_id == CaseEvent.id)
        .subquery()
    )


def _finding(findings: Any, key: str) -> Any:
    return findings.get(key) if isinstance(findings, dict) else None


def cited_txn_ids(findings: dict[str, Any] | None) -> list[int]:
    """Every transfer id the findings cite: claims, their amounts and figures."""
    if not isinstance(findings, dict):
        return []
    found: set[int] = set()

    def add(values: Any) -> None:
        if isinstance(values, list):
            for v in values:
                if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                    found.add(v)

    for claim in findings.get("claims") or []:
        if isinstance(claim, dict):
            add(claim.get("txn_ids"))
            for amount in claim.get("amounts") or []:
                if isinstance(amount, dict):
                    add([amount.get("txn_id")])
    for figure in findings.get("figures") or []:
        if isinstance(figure, dict):
            add(figure.get("txn_ids"))
    return sorted(found)


def read_tool_calls(trace_path: str | None, case_id: str) -> list[ToolCallOut] | None:
    """Tool calls of this case from the runner's results file, or None when not available.
    Only a file named results.jsonl is read; any problem means no tool call detail."""
    if not trace_path:
        return None
    path = Path(trace_path)
    if path.name != TRACE_FILE_NAME or not path.is_file():
        return None
    marker = json.dumps(case_id)
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if marker not in line:
                    continue
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("case_id") != case_id:
                    continue
                calls = record.get("trace", {}).get("tool_calls", [])
                return [
                    ToolCallOut.model_validate({k: c.get(k) for k in ToolCallOut.model_fields})
                    for c in calls
                ]
    except (OSError, ValueError, AttributeError, ValidationError):
        return None
    return None


def require_reviewer(
    settings: Annotated[ReviewerSettings, Depends(get_reviewer_settings)],
    token: Annotated[str | None, Header(alias="X-Reviewer-Token")] = None,
) -> str:
    """The reviewer name, or 403. Decisions are off unless the server has a token and a valid
    name configured; the header is compared in constant time."""
    return check_reviewer(settings, token)


def check_reviewer(settings: ReviewerSettings, token: str | None) -> str:
    if not settings.token or not settings.name or not valid_reviewer_name(settings.name):
        raise HTTPException(status_code=403, detail=DISABLED)
    given = (token or "").encode("utf-8")
    if not hmac.compare_digest(given, settings.token.encode("utf-8")):
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return settings.name.strip()


# ----------------------------------------------------------------------- endpoints


RecordsEngine = Annotated[Engine, Depends(get_records_engine)]
ToolEngine = Annotated[Engine, Depends(get_tool_engine)]


@router.get("/queue", response_model=QueuePage)
def list_queue(
    engine: RecordsEngine,
    state: QueueState = "waiting",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
) -> QueuePage:
    after = decode_cursor(cursor) if cursor else None
    decided = _first_decisions()
    stmt = select(
        AgentRun.run_id,
        AgentRun.case_id,
        AgentRun.module,
        AgentRun.status,
        AgentRun.reason,
        AgentRun.findings,
        AgentRun.created_at,
        decided.c.event,
        decided.c.actor,
        decided.c.at,
        decided.c.payload,
    ).outerjoin(decided, decided.c.run_id == AgentRun.run_id)
    if state == "waiting":
        stmt = stmt.where(decided.c.event.is_(None))
    elif state == "decided":
        stmt = stmt.where(decided.c.event.is_not(None))
    if after is not None:
        stmt = stmt.where(tuple_(AgentRun.created_at, AgentRun.run_id) < tuple_(*after))
    stmt = stmt.order_by(AgentRun.created_at.desc(), AgentRun.run_id.desc()).limit(limit + 1)

    def work() -> list[Any]:
        with engine.connect() as conn:
            return list(conn.execute(stmt).all())

    rows = _safely(work)
    items = [
        QueueItem(
            run_id=r.run_id,
            case_id=r.case_id,
            module=r.module,
            status=r.status,
            reason=r.reason,
            recommendation=_finding(r.findings, "recommendation"),
            confidence=_finding(r.findings, "confidence"),
            typology=_finding(r.findings, "typology"),
            created_at=r.created_at,
            decision=_decision_info(r.event, r.actor, r.at, r.payload),
        )
        for r in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(last.created_at, last.run_id)
    return QueuePage(state=state, items=items, next_cursor=next_cursor)


def _load_run(conn: Connection, run_id: str) -> Any:
    run = conn.execute(select(AgentRun.__table__).where(AgentRun.run_id == run_id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)
    return run


def _transfers(engine: Engine, txn_ids: list[int]) -> tuple[list[TransferRecord], list[int]]:
    found: list[TransferRecord] = []
    # Ids beyond the integer column cannot exist; asking would fail the whole transaction.
    missing: list[int] = [t for t in txn_ids if t > MAX_TXN_ID]
    wanted = [t for t in txn_ids if t <= MAX_TXN_ID]
    if not wanted:
        return found, missing
    with open_readonly(engine) as conn:
        for txn_id in wanted:
            try:
                found.append(get_transaction(conn, TransactionInput(txn_id=txn_id)))
            except ToolFailure:
                missing.append(txn_id)
    return found, sorted(missing)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, engine: RecordsEngine, tool_engine: ToolEngine) -> RunDetail:
    def work() -> tuple[Any, list[Any], list[Any]]:
        with engine.connect() as conn:
            run = _load_run(conn, run_id)
            calls = conn.execute(
                select(ModelCall.__table__).where(ModelCall.run_id == run_id).order_by(ModelCall.id)
            ).all()
            events = conn.execute(
                select(CaseEvent.__table__).where(CaseEvent.run_id == run_id).order_by(CaseEvent.id)
            ).all()
            return run, list(calls), list(events)

    run, calls, events = _safely(work)
    cited = cited_txn_ids(run.findings)
    fetch, not_fetched = cited[:MAX_CITED_LOOKUPS], cited[MAX_CITED_LOOKUPS:]
    transfers, missing = _safely(lambda: _transfers(tool_engine, fetch))
    decision = next((e for e in events if e.event in DECISION_EVENTS), None)
    return RunDetail(
        run_id=run.run_id,
        case_id=run.case_id,
        module=run.module,
        status=run.status,
        reason=run.reason,
        findings=run.findings,
        verifier_results=[VerifierOut.model_validate(v) for v in run.verifier_results],
        totals=TotalsOut.model_validate(run.totals),
        cost_usd=format(run.cost_usd, "f"),
        tool_steps=run.tool_steps,
        wall_ms=run.wall_ms,
        model=ModelInfo(profile=run.profile, provider=run.provider, model=run.model),
        prompt=PromptInfo(version=run.prompt_version, sha256=run.prompt_sha256),
        eval_run_id=run.eval_run_id,
        created_at=run.created_at,
        model_calls=[
            ModelCallOut(
                provider=c.provider,
                model=c.model,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cache_read_tokens=c.cache_read_tokens,
                cache_write_tokens=c.cache_write_tokens,
                cost_usd=format(c.cost_usd, "f"),
                latency_ms=c.latency_ms,
                stop_reason=c.stop_reason,
            )
            for c in calls
        ],
        tool_calls=read_tool_calls(run.trace_path, run.case_id),
        events=[EventOut(event=e.event, actor=e.actor, at=e.at, payload=e.payload) for e in events],
        cited_transfers=transfers,
        missing_txn_ids=missing,
        not_fetched_txn_ids=not_fetched,
        decision=None
        if decision is None
        else _decision_info(decision.event, decision.actor, decision.at, decision.payload),
    )


@router.get("/corrections", response_model=CorrectionsPage)
def list_corrections(
    engine: RecordsEngine,
    module: Annotated[str | None, Query(pattern=r"^[a-z]{1,20}$")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
) -> CorrectionsPage:
    """Saved lessons, newest first. Nothing in the agent reads them yet."""
    stmt = select(Correction.__table__)
    if cursor:
        at, raw_id = decode_cursor(cursor)
        if not raw_id.isdigit():
            raise HTTPException(status_code=422, detail="invalid cursor")
        stmt = stmt.where(tuple_(Correction.created_at, Correction.id) < tuple_(at, int(raw_id)))
    if module is not None:
        stmt = stmt.where(Correction.module == module)
    stmt = stmt.order_by(Correction.created_at.desc(), Correction.id.desc()).limit(limit + 1)

    def work() -> list[CorrectionOut]:
        with engine.connect() as conn:
            return [CorrectionOut.model_validate(r._asdict()) for r in conn.execute(stmt).all()]

    rows = _safely(work)
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor(items[-1].created_at, str(items[-1].id))
    return CorrectionsPage(items=items, next_cursor=next_cursor)


@router.post("/runs/{run_id}/decision", response_model=DecisionResponse)
def decide(
    run_id: str,
    body: DecisionRequest,
    engine: RecordsEngine,
    tool_engine: ToolEngine,
    reviewer: Annotated[str, Depends(require_reviewer)],
) -> DecisionResponse:
    event = EVENT_FOR[body.decision]
    actor = f"human:{reviewer}"
    payload: dict[str, Any] = {"decision": body.decision, "note": body.note}

    if body.edits is not None:
        edits = body.edits

        def read_run() -> Any:
            with engine.connect() as conn:
                return _load_run(conn, run_id)

        stored = _safely(read_run)
        check = check_edits(stored.module, stored.case_id, stored.findings, edits, tool_engine)
        if check.infrastructure_error:
            raise HTTPException(status_code=503, detail=DB_ERROR)
        if check.errors:
            # Nothing is written: the edited answer must pass the same checks as the agent's.
            raise HTTPException(status_code=422, detail=check.errors)
        payload |= {
            "edited": check.edited,
            "original": check.original,
            "verifier_results": [r.model_dump() for r in check.verifier_results],
        }

    def work() -> DecisionResponse:
        with engine.connect() as conn, conn.begin():
            # Take the audit log lock first, so two decisions on one run cannot both pass
            # the check below. append_case_event takes it again (it is reentrant).
            conn.execute(select(func.pg_advisory_xact_lock(CASE_EVENTS_LOCK_KEY)))
            run = _load_run(conn, run_id)
            existing = conn.execute(
                select(CaseEvent.id)
                .where(CaseEvent.run_id == run_id, CaseEvent.event.in_(DECISION_EVENTS))
                .limit(1)
            ).first()
            if existing is not None:
                raise HTTPException(status_code=409, detail="this run already has a decision")
            new_hash = append_case_event(
                conn,
                actor=actor,
                event=event,
                case_id=run.case_id,
                run_id=run_id,
                payload=payload,
            )
            correction_id = None
            if body.correction is not None:
                event_id = conn.execute(
                    select(CaseEvent.id).where(CaseEvent.hash == new_hash)
                ).scalar_one()
                correction_id = insert_correction(
                    conn,
                    body.correction,
                    module=run.module,
                    case_id=run.case_id,
                    run_id=run_id,
                    decision_event_id=event_id,
                    author=actor,
                )
            return DecisionResponse(
                run_id=run_id,
                case_id=run.case_id,
                event=event,
                actor=actor,
                hash=new_hash,
                correction_id=correction_id,
            )

    return _safely(work)
