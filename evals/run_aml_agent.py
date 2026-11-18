"""Run the AML agent over an eval dataset and write one CaseResult per alert.

Usage (from the repo root):
    uv run python -m evals.run_aml_agent --dataset evals/datasets/aml_alerts_smoke_v2.json \
        --profile scripted [--limit N] [--concurrency 1] [--out-dir evals/runs] [--allow-remote]
        [--record] [--no-review] [--reviewer-profile NAME] [--lessons on|off] [--no-trace]

Writes evals/runs/<run_id>/results.jsonl (one CaseResult per line) and manifest.json. The
agent stays read-only: this script writes files only, never the database. The database URL
comes from DATABASE_URL and is never printed; only "target: local database" or "target:
remote database" is shown, and a remote target is refused unless --allow-remote is passed.

With --record (off by default) the run is also written to the records tables through
HARNESS_WRITE_DATABASE_URL (never DATABASE_URL, and never passed to the tools server): the
eval_runs row at the start (finished fields at the end) and each case through
harness.records.record_case_result. The same local-only check applies to that URL. A case
that could not be recorded makes the script exit 1 after the run.

Maker-checker is on by default: every proposed case goes to the checker, with the same profile
unless --reviewer-profile is given. --no-review turns it off for ablations. The manifest's
"review" block records whether it was on and which profile and prompt the checker used.

Corrections memory (--lessons on, off by default): before each case the runner asks
modules/aml/memory.py for up to 5 saved reviewer lessons and the loop adds them to the first
user message (lessons_block_v1.md). Corrections are read through HARNESS_WRITE_DATABASE_URL in
read-only transactions (same local-only check) and alert detectors through DATABASE_URL. The
alert being run and every alert of the held-out dataset (HELDOUT_DATASET) are never lesson
sources. The manifest's "lessons" block records whether it was on and the source alert of
each lesson used, and each trace records the lesson ids.

Tracing: when [tracing] enabled is true in config/harness.toml and LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY and LANGFUSE_HOST are set, every case is sent to Langfuse as one trace
(harness/tracing.py), labelled with the run id and dataset. --no-trace turns it off. The
manifest's "tracing" block records whether it was on and the host (never the keys).

Cases that end in human review are a valid outcome (exit code 0). Only setup errors exit
non-zero. The last stdout line is run_dir=<path>, so CI can pick it up.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from evals.scripted_policy import ScriptedAmlPolicy
from harness.config import HarnessLimits, load_limits, load_reviewer_limits, load_tracing
from harness.db.session import (
    LOCAL_HOSTS,
    REDIRECT_QUERY_KEYS,
    DatabaseConnectionError,
    is_local_url,
    run_safely,
)
from harness.lessons import LessonsProvider
from harness.models.config import ModelProfile, Provider, get_profile
from harness.models.factory import make_adapter
from harness.models.types import ModelAdapter
from harness.prompts import LoadedPrompt, load_prompt
from harness.records import (
    WRITE_URL_ENV,
    finish_eval_run,
    get_write_engine,
    record_case_result,
    record_eval_run,
)
from harness.runner import run_case
from harness.trace import CaseResult
from harness.tracing import NOOP_TRACER, NoopTracer, Tracer, safe_tracer, tracer_from_env
from modules.aml.agent.spec import (
    AML_SPEC,
    CASE_MESSAGE_PROMPT,
    LESSONS_BLOCK_PROMPT,
    REVIEW_MESSAGE_PROMPT,
)
from modules.aml.memory import AmlLessonsProvider
from tools.aml.db import build_tool_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "evals" / "runs"
__all__ = ["LOCAL_HOSTS", "REDIRECT_QUERY_KEYS", "is_local_url"]
REQUIRED_PREFIX = "postgresql+psycopg://"
API_KEY_ENV: dict[Provider, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "scripted": None,
}
RESULTS_FILE = "results.jsonl"
MANIFEST_FILE = "manifest.json"
# Alerts of this dataset never feed lessons into any run (eval honesty, decision 0005).
HELDOUT_DATASET = REPO_ROOT / "evals" / "datasets" / "aml_alerts_heldout_v1.json"


class SetupError(Exception):
    """A mistake in the run setup. The message is safe to print (no URLs, no secrets)."""


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    version: str
    ruleset_version: str
    alert_ids: list[str] = Field(min_length=1)
    created_by: str
    split_seed: int


@dataclass(frozen=True)
class LoadedDataset:
    body: Dataset
    path: Path
    sha256: str


def target_text(url: str) -> str:
    return "target: local database" if is_local_url(url) else "target: remote database"


def check_target(url: str, allow_remote: bool, env_name: str = "DATABASE_URL") -> None:
    """Refuse a non-local database unless allowed. Runs before any connection. is_local_url
    lives in harness.db.session, so the records CLI applies the same rule."""
    if not url.startswith(REQUIRED_PREFIX):
        raise SetupError(f"{env_name} must use the postgresql+psycopg:// prefix")
    if not is_local_url(url) and not allow_remote:
        raise SetupError(
            f"{env_name} is not local (db, localhost, 127.0.0.1); pass --allow-remote to "
            "run against it on purpose"
        )


def load_dataset(path: Path) -> LoadedDataset:
    try:
        raw = path.read_bytes()
    except OSError:
        raise SetupError(f"cannot read dataset file {path}") from None
    try:
        body = Dataset.model_validate_json(raw)
    except ValueError:
        raise SetupError(f"dataset file {path.name} is not a valid dataset manifest") from None
    return LoadedDataset(body, path, hashlib.sha256(raw).hexdigest())


def check_api_key(profile: ModelProfile) -> None:
    """Fail fast when a real profile has no API key. Names the env var, never its value."""
    env = API_KEY_ENV[profile.provider]
    if env is not None and not os.environ.get(env):
        raise SetupError(f"profile {profile.name} needs {env} to be set")


def make_run_adapter(profile: ModelProfile) -> ModelAdapter:
    if profile.provider == "scripted":
        return ScriptedAmlPolicy(profile)
    return make_adapter(profile)


def new_run_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def git_state() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return done.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _prompt_info(prompt: LoadedPrompt) -> dict[str, str]:
    return {"path": prompt.path, "version": prompt.version, "sha256": prompt.sha256}


@dataclass(frozen=True)
class ReviewSetup:
    """Maker-checker settings for a run. profile is None when review is off."""

    profile: ModelProfile | None
    limits: HarnessLimits | None = None

    @property
    def enabled(self) -> bool:
        return self.profile is not None


def review_block(review: ReviewSetup) -> dict[str, Any]:
    if review.profile is None or AML_SPEC.reviewer is None:
        return {"enabled": False}
    limits = review.limits if review.limits is not None else load_reviewer_limits()
    return {
        "enabled": True,
        "model": {
            "profile": review.profile.name,
            "provider": review.profile.provider,
            "model": review.profile.model,
        },
        "prompt": _prompt_info(load_prompt(AML_SPEC.reviewer.prompt)),
        "message_prompt": _prompt_info(load_prompt(REVIEW_MESSAGE_PROMPT)),
        "limits": limits.model_dump(),
    }


@dataclass(frozen=True)
class LessonsSetup:
    """Corrections memory for a run. provider is None when lessons are off. sources is filled
    by the provider as it hands out lessons (lesson id -> source alert id)."""

    provider: LessonsProvider | None = None
    sources: dict[int, str] | None = None
    excluded_datasets: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.provider is not None


def heldout_alert_ids(path: Path = HELDOUT_DATASET) -> frozenset[str]:
    return frozenset(load_dataset(path).body.alert_ids)


def make_lessons_setup(records_engine: Engine, data_engine: Engine) -> LessonsSetup:
    """The AML provider with every held-out alert excluded as a lesson source."""
    provider = AmlLessonsProvider(records_engine, data_engine, heldout_alert_ids())
    return LessonsSetup(
        provider=provider,
        sources=provider.sources,
        excluded_datasets=(_relative(HELDOUT_DATASET),),
    )


def lessons_block(lessons: LessonsSetup, results: Sequence[CaseResult]) -> dict[str, Any]:
    if not lessons.enabled:
        return {"enabled": False}
    used = sorted({i for r in results for i in r.trace.lesson_ids})
    sources = lessons.sources or {}
    return {
        "enabled": True,
        "template": _prompt_info(load_prompt(LESSONS_BLOCK_PROMPT)),
        "excluded_source_datasets": list(lessons.excluded_datasets),
        "cases_with_lessons": sum(1 for r in results if r.trace.lesson_ids),
        # lesson id (as a JSON key) -> the alert the lesson was saved on
        "sources": {str(i): sources[i] for i in used if i in sources},
    }


def tracing_block(tracer: Tracer) -> dict[str, Any]:
    if isinstance(tracer, NoopTracer):
        return {"enabled": False, "host": None}
    return {"enabled": True, "host": tracer.host}


def _dataset_label(dataset: LoadedDataset) -> str:
    return f"{dataset.body.dataset}@{dataset.body.version}"


def _review_counts(results: Sequence[CaseResult]) -> dict[str, int]:
    verdicts = Counter(
        "failed" if r.review.verdict is None else r.review.verdict
        for r in results
        if r.review is not None
    )
    return dict(sorted(verdicts.items()))


def build_manifest(
    *,
    run_id: str,
    dataset: LoadedDataset,
    alert_ids: Sequence[str],
    profile: ModelProfile,
    limits: HarnessLimits,
    started_at: datetime,
    finished_at: datetime | None,
    results: Sequence[CaseResult],
    concurrency: int,
    review: ReviewSetup | None = None,
    lessons: LessonsSetup | None = None,
    tracer: Tracer = NOOP_TRACER,
) -> dict[str, Any]:
    prompt = load_prompt(AML_SPEC.prompt)
    case_prompt = load_prompt(CASE_MESSAGE_PROMPT)
    reasons = Counter(r.reason for r in results if r.reason is not None)
    review = review if review is not None else ReviewSetup(profile=None)
    return {
        "run_id": run_id,
        "module": AML_SPEC.name,
        "git": git_state(),
        "prompt": {"path": prompt.path, "version": prompt.version, "sha256": prompt.sha256},
        "case_message_prompt": {
            "path": case_prompt.path,
            "version": case_prompt.version,
            "sha256": case_prompt.sha256,
        },
        "model": {"profile": profile.name, "provider": profile.provider, "model": profile.model},
        "limits": limits.model_dump(),
        "review": review_block(review),
        "lessons": lessons_block(lessons if lessons is not None else LessonsSetup(), results),
        "tracing": tracing_block(tracer),
        "concurrency": concurrency,
        "dataset": {
            "name": dataset.body.dataset,
            "version": dataset.body.version,
            "ruleset_version": dataset.body.ruleset_version,
            "path": _relative(dataset.path),
            "sha256": dataset.sha256,
            "alerts_in_file": len(dataset.body.alert_ids),
            "alerts_run": len(alert_ids),
        },
        "started_at": started_at.isoformat(),
        "finished_at": None if finished_at is None else finished_at.isoformat(),
        "counts": {
            "total": len(results),
            "by_status": dict(sorted(Counter(r.status for r in results).items())),
            "by_human_review_reason": dict(sorted(reasons.items())),
            "by_review_verdict": _review_counts(results),
        },
        "total_cost_usd": str(sum((Decimal(r.cost_usd) for r in results), Decimal(0))),
    }


@dataclass
class CaseRecorder:
    """Writes each finished case through the write engine and counts failures. A failure is
    re-raised so run_case reports it; the case outcome itself is unchanged."""

    engine: Engine
    profile: ModelProfile
    prompt: LoadedPrompt
    eval_run_id: str
    trace_path: str
    recorded: int = 0
    failed: int = 0

    def __call__(self, result: CaseResult) -> str:
        def work() -> str:
            with self.engine.connect() as conn:
                return record_case_result(
                    conn,
                    result,
                    profile=self.profile,
                    prompt=self.prompt,
                    eval_run_id=self.eval_run_id,
                    trace_path=self.trace_path,
                )

        try:
            run_id = run_safely(work)
        except Exception:
            self.failed += 1
            raise
        self.recorded += 1
        return run_id


async def _run_all(
    alert_ids: Sequence[str],
    profile: ModelProfile,
    database_url: str,
    limits: HarnessLimits,
    concurrency: int,
    results_path: Path,
    recorder: CaseRecorder | None = None,
    review: ReviewSetup | None = None,
    lessons: LessonsSetup | None = None,
    tracer: Tracer = NOOP_TRACER,
    eval_run_id: str | None = None,
    dataset: str | None = None,
) -> list[CaseResult]:
    review = review if review is not None else ReviewSetup(profile=None)
    provider = None if lessons is None else lessons.provider
    gate = asyncio.Semaphore(concurrency)
    results: list[CaseResult] = []
    with results_path.open("w", encoding="utf-8") as out:

        async def one(alert_id: str) -> None:
            async with gate:
                result = await run_case(
                    alert_id,
                    AML_SPEC,
                    profile.name,
                    database_url=database_url,
                    limits=limits,
                    adapter=make_run_adapter(profile),
                    recorder=recorder,
                    review=review.enabled,
                    reviewer_profile_name=None if review.profile is None else review.profile.name,
                    reviewer_adapter=None
                    if review.profile is None
                    else make_run_adapter(review.profile),
                    reviewer_limits=review.limits,
                    lessons_provider=provider,
                    tracer=tracer,
                    eval_run_id=eval_run_id,
                    dataset=dataset,
                )
            out.write(result.model_dump_json() + "\n")
            out.flush()
            results.append(result)
            print(f"{alert_id}: {result.status} {result.reason or ''}".rstrip())

        await asyncio.gather(*(one(a) for a in alert_ids))
    return results


def run_dataset(
    dataset_path: Path,
    profile_name: str,
    database_url: str,
    *,
    limit: int | None = None,
    concurrency: int = 1,
    out_dir: Path = DEFAULT_OUT_DIR,
    limits: HarnessLimits | None = None,
    record_engine: Engine | None = None,
    review: bool = True,
    reviewer_profile_name: str | None = None,
    reviewer_limits: HarnessLimits | None = None,
    lessons: LessonsSetup | None = None,
    tracer: Tracer = NOOP_TRACER,
) -> Path:
    """Run every alert of the dataset and return the run directory. The caller has already
    checked the database targets. With record_engine, the run is also written to the records
    tables and the manifest gets a "records" block with the recorded and failed case counts.
    review=False turns the checker off; reviewer_profile_name defaults to profile_name.
    lessons (default off) turns corrections memory on for every case. tracer (default: no
    tracing) traces every case and is flushed once at the end."""
    lessons = lessons if lessons is not None else LessonsSetup()
    tracer = safe_tracer(tracer)
    if concurrency < 1:
        raise SetupError("--concurrency must be at least 1")
    if limit is not None and limit < 1:
        raise SetupError("--limit must be at least 1")
    dataset = load_dataset(dataset_path)
    try:
        profile = get_profile(profile_name)
    except KeyError as exc:
        raise SetupError(str(exc.args[0])) from None
    check_api_key(profile)
    if not review and reviewer_profile_name is not None:
        raise SetupError("--reviewer-profile cannot be used with --no-review")
    setup = ReviewSetup(profile=None)
    if review and AML_SPEC.reviewer is not None:
        try:
            reviewer_profile = get_profile(reviewer_profile_name or profile_name)
        except KeyError as exc:
            raise SetupError(str(exc.args[0])) from None
        check_api_key(reviewer_profile)
        setup = ReviewSetup(
            profile=reviewer_profile,
            limits=reviewer_limits if reviewer_limits is not None else load_reviewer_limits(),
        )
    limits = limits if limits is not None else load_limits()
    alert_ids = dataset.body.alert_ids[:limit] if limit is not None else dataset.body.alert_ids
    started_at = datetime.now(UTC)
    run_id = new_run_id(started_at)
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    recorder: CaseRecorder | None = None
    if record_engine is not None:
        engine = record_engine
        start = build_manifest(
            run_id=run_id,
            dataset=dataset,
            alert_ids=alert_ids,
            profile=profile,
            limits=limits,
            started_at=started_at,
            finished_at=None,
            results=[],
            concurrency=concurrency,
            review=setup,
            lessons=lessons,
            tracer=tracer,
        )
        start["counts"] = None
        start["total_cost_usd"] = None

        def start_run() -> None:
            with engine.connect() as conn:
                record_eval_run(conn, start)

        try:
            run_safely(start_run)
        except DatabaseConnectionError:
            raise SetupError("could not record the eval run (details hidden)") from None
        recorder = CaseRecorder(
            engine=engine,
            profile=profile,
            prompt=load_prompt(AML_SPEC.prompt),
            eval_run_id=run_id,
            trace_path=f"{_relative(run_dir)}/{RESULTS_FILE}",
        )
    try:
        results = asyncio.run(
            _run_all(
                alert_ids,
                profile,
                database_url,
                limits,
                concurrency,
                run_dir / RESULTS_FILE,
                recorder,
                setup,
                lessons,
                tracer,
                run_id,
                _dataset_label(dataset),
            )
        )
    finally:
        tracer.flush()
    manifest = build_manifest(
        run_id=run_id,
        dataset=dataset,
        alert_ids=alert_ids,
        profile=profile,
        limits=limits,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        results=results,
        concurrency=concurrency,
        review=setup,
        lessons=lessons,
        tracer=tracer,
    )
    if record_engine is not None and recorder is not None:
        engine = record_engine
        finished = manifest

        def finish_run() -> None:
            with engine.connect() as conn:
                finish_eval_run(conn, finished)

        finished_ok = True
        try:
            run_safely(finish_run)
        except (DatabaseConnectionError, RuntimeError):
            print("error: could not record the end of the eval run (details hidden)")
            finished_ok = False
        manifest["records"] = {
            "eval_run_id": run_id,
            "cases_recorded": recorder.recorded,
            "cases_failed": recorder.failed,
            "eval_run_finished": finished_ok,
        }
    (run_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return run_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.run_aml_agent")
    parser.add_argument("--dataset", type=Path, required=True, help="dataset manifest file")
    parser.add_argument("--profile", required=True, help="model profile from config/models.toml")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N alerts")
    parser.add_argument("--concurrency", type=int, default=1, help="cases run at once")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="runs folder")
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow a database that is not local"
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"also write the run to the records tables through {WRITE_URL_ENV}",
    )
    parser.add_argument(
        "--no-review", action="store_true", help="turn the checker (maker-checker) off"
    )
    parser.add_argument(
        "--reviewer-profile",
        default=None,
        help="model profile for the checker (default: the same as --profile)",
    )
    parser.add_argument(
        "--lessons",
        choices=("on", "off"),
        default="off",
        help=f"show saved reviewer lessons to the agent (reads them through {WRITE_URL_ENV})",
    )
    parser.add_argument("--no-trace", action="store_true", help="do not send this run to Langfuse")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise SetupError("DATABASE_URL is not set")
        print(target_text(url))
        check_target(url, args.allow_remote)
        record_engine: Engine | None = None
        write_engine: Engine | None = None
        data_engine: Engine | None = None
        lessons = LessonsSetup()
        if args.record or args.lessons == "on":
            flag = "--record" if args.record else "--lessons on"
            write_url = os.environ.get(WRITE_URL_ENV)
            if not write_url:
                raise SetupError(f"{flag} needs {WRITE_URL_ENV} to be set")
            print("write " + target_text(write_url))
            check_target(write_url, args.allow_remote, WRITE_URL_ENV)
            try:
                write_engine = get_write_engine(allow_remote=args.allow_remote)
            except (DatabaseConnectionError, RuntimeError) as exc:
                raise SetupError(str(exc)) from None
        if args.record:
            record_engine = write_engine
        if args.lessons == "on":
            assert write_engine is not None
            try:
                data_engine = build_tool_engine(url)
            except DatabaseConnectionError as exc:
                raise SetupError(str(exc)) from None
            lessons = make_lessons_setup(write_engine, data_engine)
        tracer = tracer_from_env(load_tracing().enabled and not args.no_trace)
        print("tracing: on" if not isinstance(tracer, NoopTracer) else "tracing: off")
        try:
            run_dir = run_dataset(
                args.dataset,
                args.profile,
                url,
                limit=args.limit,
                concurrency=args.concurrency,
                out_dir=args.out_dir,
                record_engine=record_engine,
                review=not args.no_review,
                reviewer_profile_name=args.reviewer_profile,
                lessons=lessons,
                tracer=tracer,
            )
        finally:
            for engine in (write_engine, data_engine):
                if engine is not None:
                    engine.dispose()
    except SetupError as exc:
        print(f"error: {exc}")
        return 2
    manifest = json.loads((run_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    counts = manifest["counts"]
    print(
        f"cases: {counts['total']}, by status: {counts['by_status']}, "
        f"human review reasons: {counts['by_human_review_reason']}, "
        f"review verdicts: {counts['by_review_verdict']}, "
        f"cost usd: {manifest['total_cost_usd']}"
    )
    if manifest["lessons"]["enabled"]:
        print(f"cases with lessons: {manifest['lessons']['cases_with_lessons']}")
    records = manifest.get("records")
    code = 0
    if records is not None:
        print(
            f"recorded: eval run {records['eval_run_id']}, cases {records['cases_recorded']}, "
            f"failed {records['cases_failed']}"
        )
        if records["cases_failed"] or not records["eval_run_finished"]:
            print("error: the run was not fully recorded")
            code = 1
    print(f"run_dir={_relative(run_dir)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
