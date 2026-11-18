"""The harness write path: run records, eval runs and the append-only audit log.

Only this module (plus harness/db/) writes to the records tables, and only through
HARNESS_WRITE_DATABASE_URL, never DATABASE_URL. The agent never sees this connection: the
runner gets a recorder callable from the eval runner, and the tools server is started with
DATABASE_URL and PYTHONPATH only. See docs/decisions/0007.

case_events is a hash chain: each row stores the hash of the row before it (prev_hash) and its
own hash over (prev_hash, at, actor, event, case_id, run_id, payload). Changing any old row
breaks every hash after it, and verify_chain finds the first bad row. Triggers in the
database also refuse UPDATE, DELETE and TRUNCATE on case_events, agent_runs and model_calls.

Each agent-runner event carries run_digest: the sha256 of the canonical JSON of the full
agent_runs row and its model_calls rows (in id order, without the id). verify_chain with
check_runs=True recomputes it from the current rows, so an edited run record shows up even
though the run tables are outside the chain. Known limits are in docs/decisions/0007.

A run the checker reviewed (maker-checker) also has one agent_reviews row, written in the same
transaction, and its event carries review_digest: the sha256 of the canonical JSON of that row.
verify_chain checks it the same way. Runs without a review have no review_digest, so runs
recorded before reviews existed verify exactly as before.

CLI (from the repo root):
    uv run python -m harness.records verify [--allow-remote] [--skip-run-digests]
prints "chain ok, N rows, M run digests checked, K review digests checked" or exits
non-zero. The URL is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from harness.db.records_models import (
    AGENT_RUNNER_ACTOR,
    AgentReview,
    AgentRun,
    CaseEvent,
    EvalResult,
    EvalRun,
    ModelCall,
)
from harness.db.session import (
    DatabaseConnectionError,
    engine_for_url,
    get_url_from,
    is_local_url,
    run_safely,
)
from harness.models.config import ModelProfile
from harness.prompts import LoadedPrompt
from harness.trace import CaseResult

WRITE_URL_ENV = "HARNESS_WRITE_DATABASE_URL"
# Fixed key for pg_advisory_xact_lock, so concurrent appends to case_events take turns.
CASE_EVENTS_LOCK_KEY = 0x63617365_65767473  # "caseevts"
USD_STEP = Decimal("0.000001")  # the scale of the Numeric(14, 6) money columns


class ChainReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool  # chain intact and no run digest mismatch
    rows: int
    first_bad_id: int | None
    runs_checked: int = 0
    reviews_checked: int = 0
    # case_events ids whose run records (or review record) no longer match
    run_mismatch_ids: list[int] = []


# ---------------------------------------------------------------------------------------------
# Pure part: canonical JSON and the event hash.
# ---------------------------------------------------------------------------------------------


def _default(obj: object) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise TypeError("naive datetimes are not allowed in canonical JSON")
        return obj.astimezone(UTC).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"cannot write {type(obj).__name__} as canonical JSON")


def canonical_json(obj: object) -> str:
    """One fixed text form: sorted keys, no spaces, ASCII only, Decimal as a string,
    datetimes as ISO 8601 in UTC. NaN and infinity are refused."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_default,
    )


def _jsonable(obj: object) -> Any:
    """Plain JSON data, exactly as it will come back from a JSONB column."""
    return json.loads(canonical_json(obj))


def usd(value: str | Decimal) -> Decimal:
    """A money value at the stored scale, rounded the way Postgres rounds (half up)."""
    return Decimal(value).quantize(USD_STEP, rounding=ROUND_HALF_UP)


def _digest_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: usd(v) if isinstance(v, Decimal) else v for k, v in row.items() if k != "id"}


def run_digest(run: Mapping[str, Any], calls: Sequence[Mapping[str, Any]]) -> str:
    """sha256 of the full agent_runs row plus its model_calls rows in order (ids left out,
    money at the stored scale)."""
    body = {"agent_run": _digest_row(run), "model_calls": [_digest_row(c) for c in calls]}
    return hashlib.sha256(canonical_json(body).encode("ascii")).hexdigest()


def review_digest(review: Mapping[str, Any]) -> str:
    """sha256 of the full agent_reviews row (money at the stored scale)."""
    return hashlib.sha256(canonical_json(_digest_row(review)).encode("ascii")).hexdigest()


def event_hash(
    prev_hash: str | None,
    at: datetime,
    actor: str,
    event: str,
    case_id: str,
    run_id: str | None,
    payload: Mapping[str, Any],
) -> str:
    body = {
        "prev_hash": prev_hash,
        "at": at,
        "actor": actor,
        "event": event,
        "case_id": case_id,
        "run_id": run_id,
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(body).encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------------------------
# Database part.
# ---------------------------------------------------------------------------------------------


def get_write_engine(allow_remote: bool = False) -> Engine:
    """Engine for HARNESS_WRITE_DATABASE_URL only. Never reads DATABASE_URL. A URL that is not
    local is refused unless allow_remote (the CLIs pass their --allow-remote through)."""
    url = get_url_from(WRITE_URL_ENV)
    if not is_local_url(url) and not allow_remote:
        raise RuntimeError(f"{WRITE_URL_ENV} is not local; pass --allow-remote to use it")
    return engine_for_url(url)


@contextmanager
def _atomic(conn: Connection) -> Iterator[None]:
    """One transaction for the caller's writes: a new transaction that commits on success, or
    a savepoint when the connection is already in a transaction (then the caller commits;
    note that any earlier statement on the connection opens a transaction)."""
    if conn.in_transaction():
        with conn.begin_nested():
            yield
    else:
        with conn.begin():
            yield


def append_case_event(
    conn: Connection,
    *,
    actor: str,
    event: str,
    case_id: str,
    run_id: str | None,
    payload: Mapping[str, Any],
) -> str:
    """Append one audit event and return its hash. The advisory lock is held until the
    transaction ends, so two appenders never read the same last hash."""
    data = _jsonable(dict(payload))
    with _atomic(conn):
        conn.execute(select(func.pg_advisory_xact_lock(CASE_EVENTS_LOCK_KEY)))
        prev = conn.execute(
            select(CaseEvent.hash).order_by(CaseEvent.id.desc()).limit(1)
        ).scalar_one_or_none()
        at = datetime.now(UTC)
        new_hash = event_hash(prev, at, actor, event, case_id, run_id, data)
        conn.execute(
            insert(CaseEvent).values(
                at=at,
                actor=actor,
                event=event,
                case_id=case_id,
                run_id=run_id,
                payload=data,
                prev_hash=prev,
                hash=new_hash,
            )
        )
    return new_hash


def current_run_digest(conn: Connection, run_id: str) -> str | None:
    """run_digest recomputed from the stored rows, or None when the run row is missing."""
    run = conn.execute(select(AgentRun.__table__).where(AgentRun.run_id == run_id)).first()
    if run is None:
        return None
    calls = conn.execute(
        select(ModelCall.__table__).where(ModelCall.run_id == run_id).order_by(ModelCall.id)
    ).all()
    return run_digest(dict(run._mapping), [dict(c._mapping) for c in calls])


def current_review_digest(conn: Connection, run_id: str) -> str | None:
    """review_digest recomputed from the stored row, or None when the review row is missing."""
    row = conn.execute(select(AgentReview.__table__).where(AgentReview.run_id == run_id)).first()
    return None if row is None else review_digest(dict(row._mapping))


def verify_chain(conn: Connection, *, check_runs: bool = False) -> ChainReport:
    """Recompute every hash in id order. first_bad_id is the first row whose prev_hash or
    hash does not match. Removing rows at the very end cannot be seen by a chain alone.
    With check_runs, every event that carries a run_digest is also compared with its current
    agent_runs and model_calls rows, and every event that carries a review_digest with its
    agent_reviews row; mismatching event ids are listed."""
    rows = conn.execute(
        select(
            CaseEvent.id,
            CaseEvent.at,
            CaseEvent.actor,
            CaseEvent.event,
            CaseEvent.case_id,
            CaseEvent.run_id,
            CaseEvent.payload,
            CaseEvent.prev_hash,
            CaseEvent.hash,
        )
        .order_by(CaseEvent.id)
        .execution_options(yield_per=1000)
    )
    expected_prev: str | None = None
    count = 0
    digests: list[tuple[int, str | None, Any]] = []
    review_digests: list[tuple[int, str | None, Any]] = []
    for row in rows:
        count += 1
        recomputed = event_hash(
            row.prev_hash, row.at, row.actor, row.event, row.case_id, row.run_id, row.payload
        )
        if row.prev_hash != expected_prev or row.hash != recomputed:
            rows.close()
            return ChainReport(ok=False, rows=count, first_bad_id=row.id)
        expected_prev = row.hash
        if isinstance(row.payload, dict) and "run_digest" in row.payload:
            digests.append((row.id, row.run_id, row.payload["run_digest"]))
        if isinstance(row.payload, dict) and "review_digest" in row.payload:
            review_digests.append((row.id, row.run_id, row.payload["review_digest"]))
    mismatches: set[int] = set()
    if check_runs:
        for event_id, run_id, stored in digests:
            current = None if run_id is None else current_run_digest(conn, run_id)
            if current is None or current != stored:
                mismatches.add(event_id)
        for event_id, run_id, stored in review_digests:
            current = None if run_id is None else current_review_digest(conn, run_id)
            if current is None or current != stored:
                mismatches.add(event_id)
    return ChainReport(
        ok=not mismatches,
        rows=count,
        first_bad_id=None,
        runs_checked=len(digests) if check_runs else 0,
        reviews_checked=len(review_digests) if check_runs else 0,
        run_mismatch_ids=sorted(mismatches),
    )


def _event_payload(
    result: CaseResult, run_id: str, digest: str, review: Mapping[str, Any] | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status,
        "reason": result.reason,
        "run_id": run_id,
        "run_digest": digest,
    }
    if result.findings is not None:
        for key in ("recommendation", "typology", "confidence"):
            payload[key] = result.findings.get(key)
    if result.trace.lesson_ids:
        # Lessons shown to the agent (harness.lessons). Only on runs that had lessons, so the
        # agent_runs columns and run_digest stay as they were.
        payload["lesson_ids"] = list(result.trace.lesson_ids)
    if review is not None:
        payload["review_verdict"] = review["verdict"]
        payload["review_digest"] = review_digest(review)
    return payload


def _review_row(result: CaseResult, run_id: str) -> dict[str, Any] | None:
    review = result.review
    if review is None:
        return None
    return {
        "run_id": run_id,
        "verdict": review.verdict,
        "failure_reason": review.failure_reason,
        "review": None if review.review is None else _jsonable(review.review),
        "verifier_results": _jsonable([v.model_dump() for v in review.verifier_results]),
        "usage": _jsonable(review.totals.model_dump()),
        "cost_usd": usd(review.cost_usd),
        "tool_steps": review.tool_steps,
        "wall_ms": review.wall_ms,
        "profile": review.profile,
        "provider": review.provider,
        "model": review.model,
        "prompt_version": review.prompt_version,
        "prompt_sha256": review.prompt_sha256,
        "created_at": datetime.now(UTC),
    }


def record_case_result(
    conn: Connection,
    result: CaseResult,
    *,
    profile: ModelProfile,
    prompt: LoadedPrompt,
    eval_run_id: str | None = None,
    trace_path: str | None = None,
) -> str:
    """Write agent_runs, model_calls, the checker's agent_reviews row (when the run was
    reviewed) and one case event in one transaction. Returns run_id. The rows are written once
    and never updated (triggers refuse it); the event carries their run_digest and, for a
    reviewed run, the review_digest."""
    run_id = str(uuid.uuid4())
    event = "proposal_created" if result.status == "proposed" else "human_review_created"
    run: dict[str, Any] = {
        "run_id": run_id,
        "case_id": result.case_id,
        "module": result.trace.module,
        "status": result.status,
        "reason": result.reason,
        "findings": None if result.findings is None else _jsonable(result.findings),
        "verifier_results": _jsonable([v.model_dump() for v in result.verifier_results]),
        "totals": _jsonable(result.totals.model_dump()),
        "cost_usd": usd(result.cost_usd),
        "tool_steps": result.tool_steps,
        "wall_ms": result.wall_ms,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "profile": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "eval_run_id": eval_run_id,
        "trace_path": trace_path,
        "created_at": datetime.now(UTC),
    }
    with _atomic(conn):
        conn.execute(insert(AgentRun).values(**run))
        calls = [
            {
                "run_id": run_id,
                "provider": c.provider,
                "model": c.model,
                "input_tokens": c.usage.input_tokens,
                "output_tokens": c.usage.output_tokens,
                "cache_read_tokens": c.usage.cache_read_tokens,
                "cache_write_tokens": c.usage.cache_write_tokens,
                "cost_usd": usd(c.cost_usd),
                "latency_ms": c.latency_ms,
                "stop_reason": c.stop_reason,
                "request_id": c.request_id,
            }
            for c in result.trace.model_calls
        ]
        if calls:
            conn.execute(insert(ModelCall), calls)
        review = _review_row(result, run_id)
        if review is not None:
            conn.execute(insert(AgentReview).values(**review))
        append_case_event(
            conn,
            actor=AGENT_RUNNER_ACTOR,
            event=event,
            case_id=result.case_id,
            run_id=run_id,
            payload=_event_payload(result, run_id, run_digest(run, calls), review),
        )
    return run_id


def _finished_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    finished = manifest.get("finished_at")
    cost = manifest.get("total_cost_usd")
    counts = manifest.get("counts")
    return {
        "finished_at": None if finished is None else datetime.fromisoformat(finished),
        "counts": None if counts is None else _jsonable(counts),
        "total_cost_usd": None if cost is None else Decimal(cost),
    }


def record_eval_run(conn: Connection, manifest: Mapping[str, Any]) -> str:
    """Insert the eval_runs row from a runner manifest. finished_at, counts and cost may be
    missing at the start of a run; finish_eval_run fills them in. Returns eval_run_id."""
    eval_run_id = str(manifest["run_id"])
    dataset = manifest["dataset"]
    git = manifest["git"]
    model = manifest["model"]
    prompt = manifest["prompt"]
    with _atomic(conn):
        conn.execute(
            insert(EvalRun).values(
                eval_run_id=eval_run_id,
                dataset=dataset["name"],
                dataset_version=dataset["version"],
                dataset_sha256=dataset["sha256"],
                git_commit=git.get("commit"),
                git_dirty=git.get("dirty"),
                profile=model["profile"],
                provider=model["provider"],
                model=model["model"],
                prompt_version=prompt["version"],
                prompt_sha256=prompt["sha256"],
                limits=_jsonable(manifest["limits"]),
                started_at=datetime.fromisoformat(manifest["started_at"]),
                **_finished_fields(manifest),
            )
        )
    return eval_run_id


def finish_eval_run(conn: Connection, manifest: Mapping[str, Any]) -> None:
    """Fill in finished_at, counts and total cost. eval_runs is not append-only."""
    with _atomic(conn):
        done = conn.execute(
            update(EvalRun)
            .where(EvalRun.eval_run_id == str(manifest["run_id"]))
            .values(**_finished_fields(manifest))
        )
        if done.rowcount != 1:
            raise RuntimeError("the eval run to finish is not recorded")


def eval_run_exists(conn: Connection, eval_run_id: str) -> bool:
    found = conn.execute(
        select(EvalRun.eval_run_id).where(EvalRun.eval_run_id == eval_run_id)
    ).first()
    return found is not None


def record_eval_result(
    conn: Connection, eval_run_id: str, score: Mapping[str, Any], target_recall: float
) -> None:
    """Write (or replace, when a run is scored again) the scorer output for one eval run."""
    values = {
        "eval_run_id": eval_run_id,
        "scored_at": datetime.now(UTC),
        "score": _jsonable(dict(score)),
        "target_recall": Decimal(str(target_recall)),
    }
    stmt = pg_insert(EvalResult).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[EvalResult.eval_run_id],
        set_={k: stmt.excluded[k] for k in ("scored_at", "score", "target_recall")},
    )
    with _atomic(conn):
        conn.execute(stmt)


# ---------------------------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m harness.records")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="recompute the case_events hash chain")
    verify.add_argument(
        "--allow-remote", action="store_true", help="allow a database that is not local"
    )
    verify.add_argument(
        "--skip-run-digests",
        action="store_true",
        help="check the chain only, not the run_digest of each run record",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        url = get_url_from(WRITE_URL_ENV)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2
    local = is_local_url(url)
    print("target: local database" if local else "target: remote database")
    if not local and not args.allow_remote:
        print(f"error: {WRITE_URL_ENV} is not local; pass --allow-remote to check it on purpose")
        return 2
    try:
        engine = get_write_engine(allow_remote=args.allow_remote)
        try:

            def work() -> ChainReport:
                with engine.connect() as conn:
                    return verify_chain(conn, check_runs=not args.skip_run_digests)

            report = run_safely(work)
        finally:
            engine.dispose()
    except (DatabaseConnectionError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2
    if report.first_bad_id is not None:
        print(f"chain broken at id {report.first_bad_id}, {report.rows} rows checked")
        return 1
    print(
        f"chain ok, {report.rows} rows, {report.runs_checked} run digests checked, "
        f"{report.reviews_checked} review digests checked"
    )
    if report.run_mismatch_ids:
        print(f"run records changed for case_events ids: {report.run_mismatch_ids}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
