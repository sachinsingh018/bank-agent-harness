"""Export the curated showcase runs as a static JSON bundle for the public site (replay only).

Usage:
    python -m evals.export_showcase --database-url <local url> --export-date 2026-11-19 \
        --git-commit <sha> --out apps/web/content/showcase/v1

Read only. It calls the existing API functions (list_showcase, get_run_steps, get_run)
without changing them, on an engine whose every transaction is read only, and looks up edge
transfers through the guarded tool engine. It refuses a non-local database, scripted runs,
alerts outside the development list or inside the held-out list, runs whose results.jsonl is
missing, and files over the size limits. Only allowlisted fields are written. Output is
deterministic: sorted keys, fixed separators, trailing newline, no clock reads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Engine, create_engine, select

from apps.api.queue import TRACE_FILE_NAME, RunDetail, get_run
from apps.api.watch import MAX_SHOWCASE, RunSteps, ShowcaseItem, get_run_steps, list_showcase
from harness.db.models import Alert
from harness.db.records_models import AgentRun
from harness.db.session import DatabaseConnectionError, is_local_url
from tools.aml.db import build_tool_engine, open_readonly
from tools.aml.models import TransactionInput, TransferRecord
from tools.aml.paging import ToolFailure
from tools.aml.queries import get_transaction

FORMAT = 1
SCRIPTED = "scripted"
RAW_CAP = 2000
MAX_RUN_BYTES = 120_000
MAX_BUNDLE_BYTES = 1_500_000
EVENT_PAYLOAD_KEYS = frozenset(
    {"event", "status", "reason", "recommendation", "confidence", "typology", "decision", "note"}
)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

DEV_DATASET = Path("evals/datasets/aml_alerts_dev_v1.json")
HELDOUT_DATASET = Path("evals/datasets/aml_alerts_heldout_v1.json")
SCORE_PATH = Path("evals/runs/20261118T234719Z-cd5c6c/score.json")
RULES_BASELINE = Path("evals/baselines/aml_rules_v1.json")


class ExportError(Exception):
    """The export was refused. The message says why, in plain words."""


@dataclass(frozen=True)
class Story:
    id: str
    title: str


@dataclass(frozen=True)
class Curated:
    run_id: str
    label: str
    caption: str
    story: str | None  # a Story id, or None for a single case


STORIES: tuple[Story, ...] = (
    Story("prompt-versions", "One alert, four prompt versions"),
    Story("other-detectors", "Catches on other detectors"),
)

# The gallery, in order. Labels and captions are written by hand from the stored runs.
CURATED: tuple[Curated, ...] = (
    Curated(
        "f7eb0d02-e996-4921-9278-1762e0e5ed03",
        "Caught",
        "Prompt v1 proposed to escalate this fan-out alert at confidence 0.85.",
        "prompt-versions",
    ),
    Curated(
        "823c8309-3f1d-465e-8b70-5ffb4fe19909",
        "Caught faster",
        "Prompt v2 proposed to escalate the same alert at 0.75, in about 73 seconds against "
        "187 for v1.",
        "prompt-versions",
    ),
    Curated(
        "29c00e8b-abe7-4fd9-80a1-1f920c492ab0",
        "Missed",
        "Prompt v3 proposed to close the same alert at 0.15, so it missed a real case.",
        "prompt-versions",
    ),
    Curated(
        "e75bcdb8-6779-4261-afed-3e70f759a2ff",
        "Sent to a person",
        "Prompt v5 also proposed to close it at 0.15, as v4 did. The reviewer agent ran out "
        "of time, so the case went to a person.",
        "prompt-versions",
    ),
    Curated(
        "029a419f-6e72-4ef8-85da-25a6fe1d5e8e",
        "Caught",
        "Prompt v3 proposed to escalate this scatter-gather alert at 0.9.",
        "other-detectors",
    ),
    Curated(
        "20be587f-cbd0-4b2c-ad74-fbee69513bb5",
        "Caught",
        "Prompt v3 proposed to escalate this fan-in alert at 0.75.",
        "other-detectors",
    ),
    Curated(
        "70353513-1255-4b6f-aba7-267c3402dd6e",
        "False alert closed",
        "Prompt v3 proposed to close this cycle alert at 0.15. It was a false alert.",
        None,
    ),
    Curated(
        "61e56e79-9dd9-4115-8172-36e7775ddc24",
        "Checks sent it to a person",
        "The findings failed the deterministic checks after one retry, so the case went to "
        "a person.",
        None,
    ),
)
FEATURED = "f7eb0d02-e996-4921-9278-1762e0e5ed03"


@dataclass(frozen=True)
class Sources:
    """Files the export reads besides the database. Tests pass temporary ones."""

    dev_dataset: Path = DEV_DATASET
    heldout_dataset: Path = HELDOUT_DATASET
    score: Path = SCORE_PATH
    rules_baseline: Path = RULES_BASELINE


# ------------------------------------------------------------------ helpers


def dumps(value: Any) -> bytes:
    """Stable JSON: sorted keys, compact fixed separators, UTF-8, trailing newline. Compact,
    not indented, because indenting pushes the longest curated run over MAX_RUN_BYTES."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def _read_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise ExportError(f"{what} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ExportError(f"{what} is not valid JSON: {path}") from None


def _alert_ids(path: Path, what: str) -> frozenset[str]:
    body = _read_json(path, what)
    ids = body.get("alert_ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ExportError(f"{what} has no alert_ids list: {path}")
    return frozenset(ids)


def readonly_engine(url: str) -> Engine:
    """A plain engine for a local URL where every transaction is read only."""
    if not is_local_url(url):
        raise ExportError("refusing a non-local database URL")
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


def _call[T](run_id: str, fn: Callable[..., T], *args: Any) -> T:
    try:
        return fn(*args)
    except HTTPException as exc:
        raise ExportError(f"run {run_id}: {exc.detail} (status {exc.status_code})") from None


# ------------------------------------------------------------------ run facts


@dataclass(frozen=True)
class RunFacts:
    run_id: str
    case_id: str
    profile: str
    provider: str
    trace_path: str | None
    ruleset_version: str | None


def read_run_facts(engine: Engine, run_ids: Iterable[str]) -> dict[str, RunFacts]:
    ids = list(run_ids)
    stmt = (
        select(
            AgentRun.run_id,
            AgentRun.case_id,
            AgentRun.profile,
            AgentRun.provider,
            AgentRun.trace_path,
            Alert.ruleset_version,
        )
        .outerjoin(Alert, Alert.alert_id == AgentRun.case_id)
        .where(AgentRun.run_id.in_(ids))
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    return {
        r.run_id: RunFacts(
            r.run_id, r.case_id, r.profile, r.provider, r.trace_path, r.ruleset_version
        )
        for r in rows
    }


def check_run(
    facts: RunFacts | None, run_id: str, dev: frozenset[str], held: frozenset[str]
) -> str:
    """Refuse a run that must not be shown. Returns its ruleset version."""
    if facts is None:
        raise ExportError(f"run {run_id}: not found")
    if SCRIPTED in (facts.profile, facts.provider):
        raise ExportError(f"run {run_id}: scripted runs are never exported")
    if facts.case_id in held:
        raise ExportError(f"run {run_id}: alert {facts.case_id} is held out")
    if facts.case_id not in dev:
        raise ExportError(f"run {run_id}: alert {facts.case_id} is not a development alert")
    path = Path(facts.trace_path) if facts.trace_path else None
    if path is None or path.name != TRACE_FILE_NAME or not path.is_file():
        raise ExportError(
            f"run {run_id}: {TRACE_FILE_NAME} is missing at {facts.trace_path!r}; "
            "the export needs the local eval run folder"
        )
    if facts.ruleset_version is None:
        raise ExportError(f"run {run_id}: alert {facts.case_id} is not in the alerts table")
    return facts.ruleset_version


# ------------------------------------------------------------------ transfers on edges


def _account(ref: Any) -> str:
    return f"{ref.bank}/{ref.acct}"


def lookup_transfers(tool_engine: Engine, txn_ids: Iterable[int]) -> dict[int, TransferRecord]:
    found: dict[int, TransferRecord] = {}
    with open_readonly(tool_engine) as conn:
        for txn_id in sorted(set(txn_ids)):
            try:
                found[txn_id] = get_transaction(conn, TransactionInput(txn_id=txn_id))
            except ToolFailure:
                continue
    return found


def edge_transfers(edge: dict[str, Any], known: dict[int, TransferRecord]) -> list[dict[str, Any]]:
    """Transfers of one edge that exist and run from its source to its target, in time order."""
    out: list[TransferRecord] = []
    for txn_id in edge.get("txn_ids", []):
        t = known.get(txn_id)
        if t is None:
            continue
        if _account(t.from_account) != edge["source"] or _account(t.to_account) != edge["target"]:
            continue
        out.append(t)
    out.sort(key=lambda t: (t.ts, t.txn_id))
    return [
        {
            "txn_id": t.txn_id,
            "ts": t.ts.isoformat(),
            "amount": t.amount_paid.value,
            "currency": t.amount_paid.currency,
        }
        for t in out
    ]


def add_transfers(graph: dict[str, Any], known: dict[int, TransferRecord]) -> None:
    for edge in graph.get("edges", []):
        edge["transfers"] = edge_transfers(edge, known)


def graph_txn_ids(graph: dict[str, Any]) -> set[int]:
    return {t for e in graph.get("edges", []) for t in e.get("txn_ids", [])}


# ------------------------------------------------------------------ allowlist


def steps_out(steps: RunSteps) -> dict[str, Any]:
    body = steps.model_dump(mode="json")
    for step in body["steps"]:
        raw = step.get("raw")
        if isinstance(raw, str) and len(raw) > RAW_CAP:
            step["raw"] = raw[:RAW_CAP]
            step["raw_truncated"] = True
    return body


def case_out(detail: RunDetail) -> dict[str, Any]:
    body = detail.model_dump(mode="json", exclude={"trace_id"})
    for event in body["events"]:
        payload = event.get("payload") or {}
        event["payload"] = {k: v for k, v in payload.items() if k in EVENT_PAYLOAD_KEYS}
    return body


def provenance(detail: RunDetail, ruleset_version: str) -> dict[str, Any]:
    return {
        "recorded_at": detail.created_at.isoformat(),
        "model": detail.model.model,
        "provider": detail.model.provider,
        "prompt_version": detail.prompt.version,
        "ruleset_version": ruleset_version,
    }


def effort(steps: RunSteps, detail: RunDetail) -> dict[str, Any]:
    """Tokens and latency per tool_call step, only when model calls line up one to one with
    tool_call steps; run totals always; tool durations matched to steps by order and name."""
    call_steps = [s for s in steps.steps if s.kind == "tool_call"]
    calls = detail.model_calls
    per_step: list[dict[str, Any]] | None = None
    if calls and len(calls) == len(call_steps):
        per_step = [
            {
                "step": s.index,
                "tokens_in": c.input_tokens,
                "tokens_out": c.output_tokens,
                "latency_ms": c.latency_ms,
            }
            for s, c in zip(call_steps, calls, strict=True)
        ]
    tools: list[dict[str, Any]] | None = None
    if detail.tool_calls is not None:
        aligned = len(detail.tool_calls) == len(call_steps) and all(
            t.name == s.tool for t, s in zip(detail.tool_calls, call_steps, strict=True)
        )
        tools = [
            {
                "index": t.index,
                "step": call_steps[i].index if aligned else None,
                "name": t.name,
                "ok": t.ok,
                "duration_ms": t.duration_ms,
            }
            for i, t in enumerate(detail.tool_calls)
        ]
    return {
        "per_step": per_step,
        "tools": tools,
        "totals": {
            "model_calls": len(calls),
            "tokens_in": detail.totals.input_tokens,
            "tokens_out": detail.totals.output_tokens,
            "latency_ms": sum(c.latency_ms for c in calls),
            "wall_ms": detail.wall_ms,
            "cost_usd": detail.cost_usd,
        },
    }


# ------------------------------------------------------------------ results


def _split(score: dict[str, Any], name: str) -> dict[str, Any]:
    split = score["splits"][name]
    baseline = split["baseline"]
    strict = split["agent_with_human_review"]["strict"]
    return {
        "n": split["alerts"],
        "baseline": baseline,
        "agent_with_human_review": {"strict": strict},
        "counts": {
            "false_total": baseline["false_strict"],
            "real_total": baseline["true_strict"],
            "false_closed": strict["tn"],
            "real_closed": strict["fn"],
        },
    }


def build_results(sources: Sources) -> dict[str, Any]:
    score = _read_json(sources.score, "score file")
    rules = _read_json(sources.rules_baseline, "rules baseline")
    try:
        cutoff = score["late_cutoff"]
        model = score["model"]["model"]
        splits = {name: _split(score, name) for name in ("early", "all")}
        human_review = score["splits"]["all"]["human_review"]
        alerts = rules["metrics"]["overall"]["alerts"]
        rules_171 = {
            "population": (
                f"All {rules['alert_total']} alerts raised by rules v1, development and held "
                "out together. A different population from the splits above."
            ),
            "alerts": rules["alert_total"],
            "raised": alerts["raised"],
            "precision_strict": alerts["precision_strict"],
            "true_strict": alerts["true_strict"],
            "false_strict": alerts["false_strict"],
        }
        notes = [
            "Truth is strict: an alert counts as real only when at least half of its "
            "transfers are laundering.",
            "Cases the agent sent to a person count as escalated, because a person looks at them.",
            "These are development alerts only. The prompts were improved while looking at them.",
            "There is no run on the held-out alerts yet, so these numbers may be optimistic.",
            f"The model is a free model ({model}), so the cost per case was $0.",
            f"In this synthetic dataset most transfers after {cutoff} are laundering, so late "
            "alerts are easy to call. The headline uses only alerts that end on or before "
            f"{cutoff}.",
        ]
        return {
            "eval_run_id": score["run_id"],
            "dataset": score["dataset"]["name"],
            "model": model,
            "prompt_version": score["prompt"]["version"],
            "late_cutoff": cutoff,
            "headline": "early",
            "splits": splits,
            "human_review": human_review,
            "rules_171": rules_171,
            "notes": notes,
        }
    except (KeyError, TypeError):
        raise ExportError("score file or rules baseline is missing an expected field") from None


# ------------------------------------------------------------------ the bundle


def build_bundle(
    engine: Engine,
    tool_engine: Engine,
    *,
    export_date: str,
    git_commit: str,
    sources: Sources,
    curated: tuple[Curated, ...] = CURATED,
    stories: tuple[Story, ...] = STORIES,
    featured: str = FEATURED,
) -> dict[str, bytes]:
    """Every bundle file as relative path to bytes. Nothing is written here."""
    if not DATE_PATTERN.match(export_date):
        raise ExportError("export date must look like YYYY-MM-DD")
    if not COMMIT_PATTERN.match(git_commit):
        raise ExportError("git commit must be a hex sha")
    ids = [c.run_id for c in curated]
    if len(set(ids)) != len(ids) or featured not in ids:
        raise ExportError("curated runs must be unique and include the featured run")
    story_ids = {s.id for s in stories}
    if any(c.story is not None and c.story not in story_ids for c in curated):
        raise ExportError("a curated run names an unknown story")

    results = build_results(sources)
    dev = _alert_ids(sources.dev_dataset, "development dataset")
    held = _alert_ids(sources.heldout_dataset, "held-out dataset")
    facts = read_run_facts(engine, ids)
    rulesets = {rid: check_run(facts.get(rid), rid, dev, held) for rid in ids}

    page = _call(ids[0], list_showcase, engine, MAX_SHOWCASE)
    by_id: dict[str, ShowcaseItem] = {i.run_id: i for i in page.items if i.run_id in rulesets}
    missing = [rid for rid in ids if rid not in by_id]
    if missing:
        raise ExportError(f"runs without stored steps: {', '.join(missing)}")

    files: dict[str, bytes] = {}
    showcase: list[dict[str, Any]] = []
    models: set[str] = set()
    prompts: set[str] = set()
    for rid in ids:
        steps: RunSteps = _call(rid, get_run_steps, engine, rid)
        detail: RunDetail = _call(rid, get_run, rid, engine, tool_engine)
        if SCRIPTED in (detail.model.profile, detail.model.provider):
            raise ExportError(f"run {rid}: scripted runs are never exported")
        if detail.tool_calls is None:
            raise ExportError(f"run {rid}: tool calls could not be read from {TRACE_FILE_NAME}")
        item = by_id[rid].model_dump(mode="json")
        steps_body = steps_out(steps)
        known = lookup_transfers(
            tool_engine, graph_txn_ids(item["graph"]) | graph_txn_ids(steps_body["graph"])
        )
        add_transfers(item["graph"], known)
        add_transfers(steps_body["graph"], known)
        showcase.append(item)
        run_file = dumps(
            {
                "steps": steps_body,
                "case": case_out(detail),
                "provenance": provenance(detail, rulesets[rid]),
                "effort": effort(steps, detail),
            }
        )
        if len(run_file) > MAX_RUN_BYTES:
            raise ExportError(f"run {rid}: {len(run_file)} bytes is over {MAX_RUN_BYTES}")
        files[f"runs/{rid}.json"] = run_file
        models.add(detail.model.model)
        prompts.add(detail.prompt.version)

    ruleset_versions = sorted(set(rulesets.values()))
    if len(ruleset_versions) != 1:
        raise ExportError(f"curated runs mix ruleset versions: {', '.join(ruleset_versions)}")
    manifest = {
        "format": FORMAT,
        "export_date": export_date,
        "git_commit": git_commit,
        "eval_run_id": results["eval_run_id"],
        "ruleset_version": ruleset_versions[0],
        "models": sorted(models),
        "prompt_versions": sorted(prompts),
        "featured": featured,
        "order": ids,
        "stories": [
            {
                "id": s.id,
                "title": s.title,
                "run_ids": [c.run_id for c in curated if c.story == s.id],
            }
            for s in stories
        ],
        "cases": [
            {"run_id": c.run_id, "label": c.label, "caption": c.caption, "story": c.story}
            for c in curated
        ],
    }
    files["manifest.json"] = dumps(manifest)
    files["showcase.json"] = dumps(showcase)
    files["results.json"] = dumps(results)
    total = sum(len(b) for b in files.values())
    if total > MAX_BUNDLE_BYTES:
        raise ExportError(f"bundle is {total} bytes, over {MAX_BUNDLE_BYTES}")
    return files


def write_bundle(out: Path, files: dict[str, bytes]) -> None:
    """Write the files and remove stale run files inside out/runs only."""
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    keep = {Path(name).name for name in files if name.startswith("runs/")}
    for stale in sorted(runs_dir.glob("*.json")):
        if stale.name not in keep:
            stale.unlink()
    for name in sorted(files):
        (out / name).write_bytes(files[name])


def export(
    url: str,
    out: Path,
    *,
    export_date: str,
    git_commit: str,
    sources: Sources | None = None,
    curated: tuple[Curated, ...] = CURATED,
    stories: tuple[Story, ...] = STORIES,
    featured: str = FEATURED,
) -> dict[str, bytes]:
    engine = readonly_engine(url)
    tool_engine = build_tool_engine(url)
    try:
        files = build_bundle(
            engine,
            tool_engine,
            export_date=export_date,
            git_commit=git_commit,
            sources=sources or Sources(),
            curated=curated,
            stories=stories,
            featured=featured,
        )
    finally:
        engine.dispose()
        tool_engine.dispose()
    write_bundle(out, files)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the curated showcase bundle.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--export-date", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        files = export(
            args.database_url, args.out, export_date=args.export_date, git_commit=args.git_commit
        )
    except ExportError as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 1
    except DatabaseConnectionError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    for name in sorted(files):
        print(f"{len(files[name]):>9} {name}")
    print(f"{sum(len(b) for b in files.values()):>9} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
