"""Recorded eval runs, read only: a list for the Evals page and one run with its score blocks.

Reads eval_runs and eval_results through the records engine and never writes. The scorer's
JSON holds aggregates only, but every field returned here is still whitelisted by the models
below (unknown keys are dropped), so per alert data can never leak through this endpoint even
if the score shape grows. Counts and rates only. Errors are generic.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Engine, select

from apps.api.deps import get_records_engine
from apps.api.queue import _safely
from harness.db.records_models import EvalResult, EvalRun

router = APIRouter()

MAX_EVAL_LIMIT = 50
EVAL_RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
EVAL_NOT_FOUND = "eval run not found"
HEADLINE_SPLIT = "early"

RecordsEngine = Annotated[Engine, Depends(get_records_engine)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Picked(BaseModel):
    """Read from the score JSON: keeps only the declared fields and drops everything else."""

    model_config = ConfigDict(extra="ignore")


# ------------------------------------------------------------------- score blocks


class Confusion(_Picked):
    n: int | None = None
    flagged: int | None = None
    positives: int | None = None
    tp: int | None = None
    fp: int | None = None
    fn: int | None = None
    tn: int | None = None
    precision: float | None = None
    recall: float | None = None


class TruthPair(_Picked):
    strict: Confusion | None = None
    loose: Confusion | None = None


class Baseline(_Picked):
    raised: int | None = None
    true_strict: int | None = None
    false_strict: int | None = None
    precision_strict: float | None = None
    precision_loose: float | None = None
    recall_strict: float | None = None


class HumanReview(_Picked):
    count: int | None = None
    rate: float | None = None
    reasons: dict[str, int] = {}


class FpAtRecall(_Picked):
    target_recall: float | None = None
    threshold: float | None = None
    flagged: int | None = None
    tp: int | None = None
    fp: int | None = None
    recall: float | None = None
    baseline_fp: int | None = None
    fp_reduction: float | None = None


class FpAtDecisions(_Picked):
    flagged: int | None = None
    tp: int | None = None
    fp: int | None = None
    recall: float | None = None
    baseline_fp: int | None = None
    fp_reduction: float | None = None


class FpReduction(_Picked):
    at_target_recall: FpAtRecall | None = None
    at_agent_decisions: FpAtDecisions | None = None


class Typology(_Picked):
    strict_positives_with_typology: int | None = None
    correct: int | None = None
    accuracy: float | None = None
    none_rate_on_negatives: float | None = None


class Grounding(_Picked):
    claims: int | None = None
    grounded: int | None = None
    rate: float | None = None


class Calibration(_Picked):
    n: int | None = None
    ece: float | None = None
    indicative: bool | None = None


class Effort(_Picked):
    total_cost_usd: str | None = None
    cost_per_alert_usd: str | None = None
    latency_ms_mean: float | None = None
    tool_steps_mean: float | None = None


class Review(_Picked):
    reviewed: int | None = None
    approved: int | None = None
    rejected: int | None = None
    failed: int | None = None
    reject_rate: float | None = None
    precision_change: float | None = None
    recall_change: float | None = None


class SplitScore(_Picked):
    alerts: int | None = None
    baseline: Baseline | None = None
    agent_with_human_review: TruthPair | None = None
    agent_proposed_only: TruthPair | None = None
    human_review: HumanReview | None = None
    fp_reduction: FpReduction | None = None
    typology: Typology | None = None
    grounding: Grounding | None = None
    calibration: Calibration | None = None
    effort: Effort | None = None
    review: Review | None = None


class Splits(_Picked):
    early: SplitScore | None = None
    late: SplitScore | None = None
    all: SplitScore | None = None


class LessonsUse(_Picked):
    enabled: bool | None = None
    cases_with_lessons: int | None = None


class ScoreOut(_Picked):
    headline: str | None = None
    late_cutoff: str | None = None
    target_recall: float | None = None
    splits: Splits | None = None
    lessons: LessonsUse | None = None
    notes: list[str] = []


class RunCounts(_Picked):
    total: int | None = None
    by_status: dict[str, int] = {}
    by_human_review_reason: dict[str, int] = {}
    by_review_verdict: dict[str, int] = {}


# ------------------------------------------------------------------- response models


class Headline(_Out):
    """The early window (decision 0006) against the rules baseline, strict truth."""

    split: str
    alerts: int | None
    precision: float | None
    recall: float | None
    baseline_precision: float | None
    human_review_rate: float | None
    reviewer_reject_rate: float | None


class EvalRunSummary(_Out):
    eval_run_id: str
    started_at: datetime
    finished_at: datetime | None
    dataset: str
    dataset_version: str
    provider: str
    model: str
    prompt_version: str
    cases: int | None
    total_cost_usd: str | None
    scored: bool
    headline: Headline | None


class EvalRunsPage(_Out):
    items: list[EvalRunSummary]


class EvalRunDetail(EvalRunSummary):
    dataset_sha256: str
    git_commit: str | None
    git_dirty: bool | None
    profile: str
    prompt_sha256: str
    limits: dict[str, int]
    counts: RunCounts | None
    scored_at: datetime | None
    score: ScoreOut | None


# ------------------------------------------------------------------- helpers


def read_score(raw: Any) -> ScoreOut | None:
    if not isinstance(raw, dict):
        return None
    try:
        return ScoreOut.model_validate(raw)
    except ValidationError:
        return None


def read_counts(raw: Any) -> RunCounts | None:
    if not isinstance(raw, dict):
        return None
    try:
        return RunCounts.model_validate(raw)
    except ValidationError:
        return None


def read_limits(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, int) and not isinstance(v, bool)}


def headline_of(score: ScoreOut | None) -> Headline | None:
    if score is None or score.splits is None:
        return None
    split = score.splits.early
    if split is None:
        return None
    strict = split.agent_with_human_review.strict if split.agent_with_human_review else None
    return Headline(
        split=HEADLINE_SPLIT,
        alerts=split.alerts,
        precision=strict.precision if strict else None,
        recall=strict.recall if strict else None,
        baseline_precision=split.baseline.precision_strict if split.baseline else None,
        human_review_rate=split.human_review.rate if split.human_review else None,
        reviewer_reject_rate=split.review.reject_rate if split.review else None,
    )


def _usd(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _summary_fields(row: Any, score: ScoreOut | None, counts: RunCounts | None) -> dict[str, Any]:
    return {
        "eval_run_id": row.eval_run_id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "dataset": row.dataset,
        "dataset_version": row.dataset_version,
        "provider": row.provider,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "cases": counts.total if counts else None,
        "total_cost_usd": _usd(row.total_cost_usd),
        "scored": row.score is not None,
        "headline": headline_of(score),
    }


def _select() -> Any:
    return select(
        EvalRun.eval_run_id,
        EvalRun.started_at,
        EvalRun.finished_at,
        EvalRun.dataset,
        EvalRun.dataset_version,
        EvalRun.dataset_sha256,
        EvalRun.git_commit,
        EvalRun.git_dirty,
        EvalRun.profile,
        EvalRun.provider,
        EvalRun.model,
        EvalRun.prompt_version,
        EvalRun.prompt_sha256,
        EvalRun.limits,
        EvalRun.counts,
        EvalRun.total_cost_usd,
        EvalResult.scored_at,
        EvalResult.score,
    ).outerjoin(EvalResult, EvalResult.eval_run_id == EvalRun.eval_run_id)


# ------------------------------------------------------------------- endpoints


@router.get("/evals", response_model=EvalRunsPage)
def list_evals(
    engine: RecordsEngine,
    limit: Annotated[int, Query(ge=1, le=MAX_EVAL_LIMIT)] = MAX_EVAL_LIMIT,
) -> EvalRunsPage:
    """Recorded eval runs, newest first, with the headline numbers when scored."""
    stmt = _select().order_by(EvalRun.started_at.desc(), EvalRun.eval_run_id.desc()).limit(limit)

    def work() -> list[Any]:
        with engine.connect() as conn:
            return list(conn.execute(stmt).all())

    rows = _safely(work)
    items = [
        EvalRunSummary(**_summary_fields(r, read_score(r.score), read_counts(r.counts)))
        for r in rows
    ]
    return EvalRunsPage(items=items)


@router.get("/evals/{eval_run_id}", response_model=EvalRunDetail)
def get_eval(
    engine: RecordsEngine,
    eval_run_id: Annotated[str, Path(pattern=EVAL_RUN_ID_PATTERN)],
) -> EvalRunDetail:
    """One eval run: manifest fields, run counts and the whitelisted score blocks."""
    stmt = _select().where(EvalRun.eval_run_id == eval_run_id)

    def work() -> Any:
        with engine.connect() as conn:
            return conn.execute(stmt).first()

    row = _safely(work)
    if row is None:
        raise HTTPException(status_code=404, detail=EVAL_NOT_FOUND)
    score = read_score(row.score)
    counts = read_counts(row.counts)
    return EvalRunDetail(
        **_summary_fields(row, score, counts),
        dataset_sha256=row.dataset_sha256,
        git_commit=row.git_commit,
        git_dirty=row.git_dirty,
        profile=row.profile,
        prompt_sha256=row.prompt_sha256,
        limits=read_limits(row.limits),
        counts=counts,
        scored_at=row.scored_at,
        score=score,
    )
