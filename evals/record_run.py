"""Record an existing run directory into the records tables. No model calls.

Usage (from the repo root):
    uv run python -m evals.record_run --run evals/runs/<run_id> [--score] [--allow-remote]

Reads manifest.json and results.jsonl from the run directory and writes, in one transaction:
the eval_runs row (with the original run id and times), one agent run per case through
harness.records.record_case_result, and the finished fields. With --score, score.json from
the run directory is also written to eval_results when the file exists. This is how runs made
locally are copied to the hosted database without spending model requests.

The write target comes from HARNESS_WRITE_DATABASE_URL only (never DATABASE_URL) and is never
printed; only "write target: local database" or "write target: remote database" is shown. A
remote target is refused unless --allow-remote is passed. A run id that is already recorded
there is refused. Labels are never read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evals.run_aml_agent import (
    MANIFEST_FILE,
    RESULTS_FILE,
    SetupError,
    _relative,
    check_target,
    target_text,
)
from harness.db.session import DatabaseConnectionError, run_safely
from harness.models.config import ModelProfile, Prices
from harness.prompts import LoadedPrompt
from harness.records import (
    WRITE_URL_ENV,
    eval_run_exists,
    finish_eval_run,
    get_write_engine,
    record_case_result,
    record_eval_result,
    record_eval_run,
)
from harness.trace import CaseResult

__all__ = ["SetupError", "main", "read_run_dir", "record_files"]

SCORE_FILE = "score.json"


@dataclass(frozen=True)
class RunFiles:
    manifest: dict[str, Any]
    results: list[CaseResult]
    score: dict[str, Any] | None


def read_run_dir(run_dir: Path, with_score: bool) -> RunFiles:
    """Read the run files. Every CaseResult line is validated by the Pydantic model."""
    try:
        manifest = json.loads((run_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        lines = (run_dir / RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        raise SetupError(f"cannot read the run files in {run_dir}") from None
    try:
        results = [CaseResult.model_validate_json(line) for line in lines if line.strip()]
    except ValidationError:
        bad = f"{RESULTS_FILE} in {run_dir} has a line that is not a CaseResult"
        raise SetupError(bad) from None
    if not results:
        raise SetupError(f"{RESULTS_FILE} in {run_dir} has no results")
    score: dict[str, Any] | None = None
    score_path = run_dir / SCORE_FILE
    if with_score and score_path.exists():
        try:
            score = json.loads(score_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SetupError(f"cannot read {SCORE_FILE} in {run_dir}") from None
        if not isinstance(score, dict) or score.get("run_id") != manifest.get("run_id"):
            raise SetupError(f"{SCORE_FILE} does not belong to this run")
    return RunFiles(manifest, results, score)


def profile_from_manifest(manifest: dict[str, Any]) -> ModelProfile:
    """Only name, provider and model are recorded, so the other fields are placeholders. The
    profile is rebuilt from the manifest, not from config, because config may have changed."""
    model = manifest["model"]
    zero = Decimal(0)
    prices = Prices(input=zero, output=zero, cache_read=zero, cache_write=zero)
    return ModelProfile(
        name=model["profile"],
        provider=model["provider"],
        model=model["model"],
        max_output_tokens=1,
        prices=prices,
    )


def prompt_from_manifest(manifest: dict[str, Any]) -> LoadedPrompt:
    """Only version and sha256 are recorded; the text is not needed."""
    prompt = manifest["prompt"]
    return LoadedPrompt(
        text="", version=prompt["version"], path=prompt["path"], sha256=prompt["sha256"]
    )


def record_files(files: RunFiles, run_dir: Path, allow_remote: bool) -> int:
    """Write everything in one transaction. Returns the number of cases recorded."""
    manifest = files.manifest
    eval_run_id = str(manifest["run_id"])
    profile = profile_from_manifest(manifest)
    prompt = prompt_from_manifest(manifest)
    trace_path = f"{_relative(run_dir)}/{RESULTS_FILE}"
    engine = get_write_engine(allow_remote=allow_remote)
    try:

        def work() -> bool:
            with engine.begin() as conn:
                if eval_run_exists(conn, eval_run_id):
                    return False
                record_eval_run(conn, manifest)
                for result in files.results:
                    record_case_result(
                        conn,
                        result,
                        profile=profile,
                        prompt=prompt,
                        eval_run_id=eval_run_id,
                        trace_path=trace_path,
                    )
                finish_eval_run(conn, manifest)
                if files.score is not None:
                    target = float(files.score["target_recall"])
                    record_eval_result(conn, eval_run_id, files.score, target)
                return True

        if not run_safely(work):
            raise SetupError(f"eval run {eval_run_id} is already recorded; nothing written")
    finally:
        engine.dispose()
    return len(files.results)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.record_run")
    parser.add_argument("--run", type=Path, required=True, help="run directory")
    parser.add_argument(
        "--score", action="store_true", help=f"also record {SCORE_FILE} when it exists"
    )
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow a database that is not local"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    write_url = os.environ.get(WRITE_URL_ENV)
    try:
        if not write_url:
            raise SetupError(f"{WRITE_URL_ENV} is not set")
        print("write " + target_text(write_url))
        check_target(write_url, args.allow_remote, WRITE_URL_ENV)
        files = read_run_dir(args.run, args.score)
        cases = record_files(files, args.run, args.allow_remote)
    except (SetupError, DatabaseConnectionError, RuntimeError, KeyError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    scored = "with score" if files.score is not None else "without score"
    print(f"recorded: eval run {files.manifest['run_id']}, cases {cases}, {scored}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
