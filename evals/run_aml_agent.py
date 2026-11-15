"""Run the AML agent over an eval dataset and write one CaseResult per alert.

Usage (from the repo root):
    uv run python -m evals.run_aml_agent --dataset evals/datasets/aml_alerts_smoke_v1.json \
        --profile scripted [--limit N] [--concurrency 1] [--out-dir evals/runs] [--allow-remote]

Writes evals/runs/<run_id>/results.jsonl (one CaseResult per line) and manifest.json. The
agent stays read-only: this script writes files only, never the database. The database URL
comes from DATABASE_URL and is never printed; only "target: local database" or "target:
remote database" is shown, and a remote target is refused unless --allow-remote is passed.

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
from sqlalchemy.engine import make_url

from evals.scripted_policy import ScriptedAmlPolicy
from harness.config import HarnessLimits, load_limits
from harness.models.config import ModelProfile, Provider, get_profile
from harness.models.factory import make_adapter
from harness.models.types import ModelAdapter
from harness.prompts import load_prompt
from harness.runner import run_case
from harness.trace import CaseResult
from modules.aml.agent.spec import AML_SPEC, CASE_MESSAGE_PROMPT

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "evals" / "runs"
LOCAL_HOSTS = ("db", "localhost", "127.0.0.1")
REDIRECT_QUERY_KEYS = ("host", "hostaddr", "service")
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


def is_local_url(url: str) -> bool:
    """True only for a single plain local host. A missing host (socket or env defaults), a
    host list, or a query that can redirect the connection (host, hostaddr, service) counts
    as not local, so it needs --allow-remote."""
    try:
        parsed = make_url(url)
    except Exception:
        return False
    host = parsed.host
    if not host or "," in host:
        return False
    if any(key in parsed.query for key in REDIRECT_QUERY_KEYS):
        return False
    return host in LOCAL_HOSTS


def target_text(url: str) -> str:
    return "target: local database" if is_local_url(url) else "target: remote database"


def check_target(url: str, allow_remote: bool) -> None:
    """Refuse a non-local database unless allowed. Runs before any connection."""
    if not url.startswith(REQUIRED_PREFIX):
        raise SetupError("DATABASE_URL must use the postgresql+psycopg:// prefix")
    if not is_local_url(url) and not allow_remote:
        raise SetupError(
            "the database is not local (db, localhost, 127.0.0.1); pass --allow-remote to "
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
    finished_at: datetime,
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
        "finished_at": finished_at.isoformat(),
        "counts": {
            "total": len(results),
            "by_status": dict(sorted(Counter(r.status for r in results).items())),
            "by_human_review_reason": dict(sorted(reasons.items())),
        },
        "total_cost_usd": str(sum((Decimal(r.cost_usd) for r in results), Decimal(0))),
    }


async def _run_all(
    alert_ids: Sequence[str],
    profile: ModelProfile,
    database_url: str,
    limits: HarnessLimits,
    concurrency: int,
    results_path: Path,
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
) -> Path:
    """Run every alert of the dataset and return the run directory. The caller has already
    checked the database target."""
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
    results = asyncio.run(
        _run_all(alert_ids, profile, database_url, limits, concurrency, run_dir / RESULTS_FILE)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise SetupError("DATABASE_URL is not set")
        print(target_text(url))
        check_target(url, args.allow_remote)
        run_dir = run_dataset(
            args.dataset,
            args.profile,
            url,
            limit=args.limit,
            concurrency=args.concurrency,
            out_dir=args.out_dir,
        )
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
    print(f"run_dir={_relative(run_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
