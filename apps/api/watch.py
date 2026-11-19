"""Watch an investigation: recorded runs as replayable steps, read only.

GET /showcase lists every recorded run that has stored steps (run_traces), newest first, with
its alert and the alert's money flow for the gallery. It reads only the small case card of each
trace, never the whole trace. GET /runs/{run_id}/steps turns one stored trace into display
steps and the growing money-flow graph (apps/api/replay.py). Both read the records connection
and never write. Errors are generic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Engine, select

from apps.api.deps import get_records_engine
from apps.api.queue import ModelInfo, ReviewState, _safely, review_state
from apps.api.replay import (
    REASON_TEXT,
    AlertCard,
    FlowGraph,
    ReplayStep,
    alert_card,
    build_replay,
    card_graph,
)
from harness.db.records_models import AgentReview, AgentRun, RunTraceRow
from harness.run_traces import StoredTrace

router = APIRouter()

RUN_ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"
MAX_SHOWCASE = 200
NO_STEPS = "no stored steps for this run"

RecordsEngine = Annotated[Engine, Depends(get_records_engine)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShowcaseItem(_Out):
    run_id: str
    case_id: str
    created_at: datetime
    status: str
    reason: str | None
    recommendation: str | None
    confidence: float | None
    typology: str | None
    review_verdict: ReviewState | None
    tool_steps: int
    model: ModelInfo
    alert: AlertCard
    graph: FlowGraph
    teaser: str


class ShowcasePage(_Out):
    items: list[ShowcaseItem]


class RunSteps(_Out):
    run_id: str
    case_id: str
    module: str
    created_at: datetime
    status: str
    reason: str | None
    model: ModelInfo
    eval_run_id: str | None
    tool_steps: int
    wall_ms: int
    cost_usd: str
    alert: AlertCard
    steps: list[ReplayStep]
    graph: FlowGraph


def _finding(findings: Any, key: str) -> Any:
    return findings.get(key) if isinstance(findings, dict) else None


def teaser(status: str, reason: str | None, recommendation: str | None, tool_steps: int) -> str:
    """One plain line about how the case went, written by code."""
    calls = f"{tool_steps} tool call{'' if tool_steps == 1 else 's'}"
    if status == "proposed":
        verb = {"escalate": "recommended escalation", "close": "recommended closure"}.get(
            recommendation or "", "made a recommendation"
        )
        return f"{calls}, then {verb}. An analyst makes the final decision."
    why = REASON_TEXT.get(reason or "", "it needed an analyst")
    return f"{calls}, then routed to analyst review: {why}."


@router.get("/showcase", response_model=ShowcasePage)
def list_showcase(
    engine: RecordsEngine,
    limit: Annotated[int, Query(ge=1, le=MAX_SHOWCASE)] = MAX_SHOWCASE,
) -> ShowcasePage:
    """Recorded runs with stored steps, newest first."""
    stmt = (
        select(
            AgentRun.run_id,
            AgentRun.case_id,
            AgentRun.created_at,
            AgentRun.status,
            AgentRun.reason,
            AgentRun.findings,
            AgentRun.tool_steps,
            AgentRun.profile,
            AgentRun.provider,
            AgentRun.model,
            RunTraceRow.case_card,
            AgentReview.run_id.label("review_run_id"),
            AgentReview.verdict.label("review_verdict"),
        )
        .join(RunTraceRow, RunTraceRow.run_id == AgentRun.run_id)
        .outerjoin(AgentReview, AgentReview.run_id == AgentRun.run_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.run_id.desc())
        .limit(limit)
    )

    def work() -> list[Any]:
        with engine.connect() as conn:
            return list(conn.execute(stmt).all())

    rows = _safely(work)
    items: list[ShowcaseItem] = []
    for r in rows:
        recommendation = _finding(r.findings, "recommendation")
        confidence = _finding(r.findings, "confidence")
        items.append(
            ShowcaseItem(
                run_id=r.run_id,
                case_id=r.case_id,
                created_at=r.created_at,
                status=r.status,
                reason=r.reason,
                recommendation=recommendation if isinstance(recommendation, str) else None,
                confidence=float(confidence) if isinstance(confidence, int | float) else None,
                typology=_finding(r.findings, "typology"),
                review_verdict=review_state(r.review_verdict, r.review_run_id is not None),
                tool_steps=r.tool_steps,
                model=ModelInfo(profile=r.profile, provider=r.provider, model=r.model),
                alert=alert_card(r.case_card, r.case_id),
                graph=card_graph(r.case_card),
                teaser=teaser(r.status, r.reason, recommendation, r.tool_steps),
            )
        )
    return ShowcasePage(items=items)


@router.get("/runs/{run_id}/steps", response_model=RunSteps)
def get_run_steps(
    engine: RecordsEngine,
    run_id: Annotated[str, Path(pattern=RUN_ID_PATTERN)],
) -> RunSteps:
    """One recorded run as ordered display steps with its money-flow graph."""

    def work() -> tuple[Any, Any]:
        with engine.connect() as conn:
            run = conn.execute(
                select(AgentRun.__table__, RunTraceRow.trace, RunTraceRow.case_card)
                .join(RunTraceRow, RunTraceRow.run_id == AgentRun.run_id)
                .where(AgentRun.run_id == run_id)
            ).first()
            review = conn.execute(
                select(AgentReview.__table__).where(AgentReview.run_id == run_id)
            ).first()
            return run, review

    run, review = _safely(work)
    if run is None:
        raise HTTPException(status_code=404, detail=NO_STEPS)
    try:
        stored = StoredTrace.model_validate(run.trace)
    except ValidationError:
        raise HTTPException(status_code=404, detail=NO_STEPS) from None
    run_fields = dict(run._mapping)
    review_fields = None if review is None else dict(review._mapping)
    alert, steps, graph = build_replay(run_fields, stored, run.case_card, review_fields)
    return RunSteps(
        run_id=run.run_id,
        case_id=run.case_id,
        module=run.module,
        created_at=run.created_at,
        status=run.status,
        reason=run.reason,
        model=ModelInfo(profile=run.profile, provider=run.provider, model=run.model),
        eval_run_id=run.eval_run_id,
        tool_steps=run.tool_steps,
        wall_ms=run.wall_ms,
        cost_usd=format(run.cost_usd, "f"),
        alert=alert,
        steps=steps,
        graph=graph,
    )
