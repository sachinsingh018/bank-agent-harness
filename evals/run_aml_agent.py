"""Run the AML agent over an eval dataset and write one CaseResult per alert.

Usage (from the repo root):
    uv run python -m evals.run_aml_agent --dataset evals/datasets/aml_alerts_smoke_v1.json \
        --profile scripted [--limit N] [--concurrency 1] [--out-dir evals/runs] [--allow-remote]
        [--record]

Writes evals/runs/<run_id>/results.jsonl (one CaseResult per line) and manifest.json. The
agent stays read-only: this script writes files only, never the database. The database URL
comes from DATABASE_URL and is never printed; only "target: local database" or "target:
remote database" is shown, and a remote target is refused unless --allow-remote is passed.

With --record (off by default) the run is also written to the records tables through
HARNESS_WRITE_DATABASE_URL (never DATABASE_URL, and never passed to the tools server): the
eval_runs row at the start (finished fields at the end) and each case through
harness.records.record_case_result. The same local-only check applies to that URL. A case
that could not be recorded makes the script exit 1 after the run.

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
from harness.config import HarnessLimits, load_limits
from harness.db.session import (
    LOCAL_HOSTS,
    REDIRECT_QUERY_KEYS,
    DatabaseConnectionError,
    is_local_url,
    run_safely,
)
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
from modules.aml.agent.spec import AML_SPEC, CASE_MESSAGE_PROMPT

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
) -> dict[str, Any]:
    prompt = load_prompt(AML_SPEC.prompt)
    case_prompt = load_prompt(CASE_MESSAGE_PROMPT)
    reasons = Counter(r.reason for r in results if r.reason is not None)
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
) -> list[CaseResult]:
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
) -> Path:
    """Run every alert of the dataset and return the run directory. The caller has already
    checked the database targets. With record_engine, the run is also written to the records
    tables and the manifest gets a "records" block with the recorded and failed case counts."""
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
    results = asyncio.run(
        _run_all(
            alert_ids,
            profile,
            database_url,
            limits,
            concurrency,
            run_dir / RESULTS_FILE,
            recorder,
        )
    )
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
        if args.record:
            write_url = os.environ.get(WRITE_URL_ENV)
            if not write_url:
                raise SetupError(f"--record needs {WRITE_URL_ENV} to be set")
            print("write " + target_text(write_url))
            check_target(write_url, args.allow_remote, WRITE_URL_ENV)
            try:
                record_engine = get_write_engine(allow_remote=args.allow_remote)
            except (DatabaseConnectionError, RuntimeError) as exc:
                raise SetupError(str(exc)) from None
        try:
            run_dir = run_dataset(
                args.dataset,
                args.profile,
                url,
                limit=args.limit,
                concurrency=args.concurrency,
                out_dir=args.out_dir,
                record_engine=record_engine,
            )
        finally:
            if record_engine is not None:
                record_engine.dispose()
    except SetupError as exc:
        print(f"error: {exc}")
        return 2
    manifest = json.loads((run_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    counts = manifest["counts"]
    print(
        f"cases: {counts['total']}, by status: {counts['by_status']}, "
        f"human review reasons: {counts['by_human_review_reason']}, "
        f"cost usd: {manifest['total_cost_usd']}"
    )
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
