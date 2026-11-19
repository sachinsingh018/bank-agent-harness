"""Pure tests for the showcase exporter: allowlist, refusals, results and stable output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.api.queue import (
    EventOut,
    ModelCallOut,
    ModelInfo,
    PromptInfo,
    RunDetail,
    ToolCallOut,
    TotalsOut,
)
from apps.api.replay import AlertCard, FlowGraph, ReplayStep
from apps.api.watch import RunSteps
from evals import export_showcase as ex
from tools.aml.models import AccountRef, Money, TransferRecord

NOW = datetime(2026, 11, 16, 1, 2, 3, tzinfo=UTC)
DASHES = (chr(0x2014), chr(0x2013))  # em and en dash, written as code points


def _steps(raw: str = "x") -> RunSteps:
    return RunSteps(
        run_id="r1",
        case_id="AL-1",
        module="aml",
        created_at=NOW,
        status="proposed",
        reason=None,
        model=ModelInfo(profile="p", provider="openrouter", model="m"),
        eval_run_id=None,
        tool_steps=1,
        wall_ms=10,
        cost_usd="0",
        alert=AlertCard(
            alert_id="AL-1",
            detector=None,
            detector_label=None,
            summary=None,
            anchor=None,
            txn_count=None,
            account_count=None,
            window_start=None,
            window_end=None,
        ),
        steps=[
            ReplayStep(index=0, kind="alert", actor="rule", title="a", explanation="e"),
            ReplayStep(
                index=1,
                kind="tool_call",
                actor="agent",
                title="t",
                explanation="e",
                tool="get_alert",
                raw=raw,
            ),
            ReplayStep(
                index=2,
                kind="tool_call",
                actor="agent",
                title="t",
                explanation="e",
                tool="submit_findings",
            ),
        ],
        graph=FlowGraph(nodes=[], edges=[], hidden_accounts=0),
    )


def _call(tokens: int) -> ModelCallOut:
    return ModelCallOut(
        provider="openrouter",
        model="m",
        input_tokens=tokens,
        output_tokens=tokens // 10,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd="0",
        latency_ms=tokens * 2,
        stop_reason="tool_use",
    )


def _tool(index: int, name: str, step: int | None) -> ToolCallOut:
    return ToolCallOut(
        index=index,
        step=step,
        name=name,
        arguments={},
        ok=True,
        error_code=None,
        duration_ms=5 + index,
        size=1,
        truncated=False,
    )


def _detail(calls: int = 2) -> RunDetail:
    return RunDetail(
        run_id="r1",
        case_id="AL-1",
        module="aml",
        status="proposed",
        reason=None,
        findings={"recommendation": "close"},
        verifier_results=[],
        totals=TotalsOut(
            input_tokens=30, output_tokens=3, cache_read_tokens=0, cache_write_tokens=0
        ),
        cost_usd="0",
        tool_steps=1,
        wall_ms=10,
        model=ModelInfo(profile="p", provider="openrouter", model="m"),
        prompt=PromptInfo(version="v3", sha256="0" * 64),
        eval_run_id=None,
        created_at=NOW,
        model_calls=[_call(10 * (i + 1)) for i in range(calls)],
        tool_calls=[_tool(0, "get_alert", 1), _tool(1, "submit_findings", None)],
        events=[
            EventOut(
                event="proposal_created",
                actor="agent-runner",
                at=NOW,
                payload={"status": "proposed", "run_digest": "abc", "trace_id": "t", "note": "n"},
            )
        ],
        cited_transfers=[],
        missing_txn_ids=[],
        not_fetched_txn_ids=[],
        decision=None,
        review=None,
        lessons=[],
        trace_id="langfuse-123",
    )


def _facts(**kw: Any) -> ex.RunFacts:
    base: dict[str, Any] = {
        "run_id": "r1",
        "case_id": "AL-dev",
        "profile": "openrouter-free-dev",
        "provider": "openrouter",
        "trace_path": None,
        "ruleset_version": "v1",
    }
    base.update(kw)
    return ex.RunFacts(**base)


@pytest.fixture()
def results_file(tmp_path: Path) -> Path:
    path = tmp_path / "results.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    return path


DEV = frozenset({"AL-dev"})
HELD = frozenset({"AL-held"})


# ------------------------------------------------------------------ allowlist


def test_raw_is_capped_with_flag() -> None:
    body = ex.steps_out(_steps(raw="y" * (ex.RAW_CAP + 50)))
    step = body["steps"][1]
    assert len(step["raw"]) == ex.RAW_CAP and step["raw_truncated"] is True
    short = ex.steps_out(_steps(raw="short"))["steps"][1]
    assert short["raw"] == "short" and "raw_truncated" not in short


def test_case_drops_trace_id_and_filters_payload() -> None:
    body = ex.case_out(_detail())
    assert "trace_id" not in body
    assert body["events"][0]["payload"] == {"status": "proposed", "note": "n"}
    required = set(RunDetail.model_fields) - {"trace_id"}
    assert required <= set(body)


def test_effort_maps_calls_only_when_counts_match() -> None:
    steps = _steps()
    good = ex.effort(steps, _detail(calls=2))
    assert good["per_step"] == [
        {"step": 1, "tokens_in": 10, "tokens_out": 1, "latency_ms": 20},
        {"step": 2, "tokens_in": 20, "tokens_out": 2, "latency_ms": 40},
    ]
    assert [t["step"] for t in good["tools"]] == [1, 2]
    assert good["totals"]["latency_ms"] == 60 and good["totals"]["model_calls"] == 2
    bad = ex.effort(steps, _detail(calls=3))
    assert bad["per_step"] is None and bad["totals"]["model_calls"] == 3


def _transfer(txn_id: int, day: int, src: str, dst: str) -> TransferRecord:
    def ref(text: str) -> AccountRef:
        bank, acct = text.split("/")
        return AccountRef(bank=int(bank), acct=acct)

    return TransferRecord(
        txn_id=txn_id,
        ts=datetime(2022, 9, day, 10, 0),
        from_account=ref(src),
        to_account=ref(dst),
        amount_paid=Money.of(Decimal("12.50"), "US Dollar"),
        amount_received=Money.of(Decimal("11.00"), "Euro"),
        format="ACH",
    )


def test_edge_transfers_sorted_paid_side_and_matching_direction() -> None:
    known = {
        7: _transfer(7, 3, "1/A", "2/B"),
        5: _transfer(5, 3, "1/A", "2/B"),
        9: _transfer(9, 1, "1/A", "2/B"),
        8: _transfer(8, 1, "2/B", "1/A"),
    }
    edge = {"source": "1/A", "target": "2/B", "txn_ids": [7, 5, 9, 8, 404]}
    out = ex.edge_transfers(edge, known)
    assert [t["txn_id"] for t in out] == [9, 5, 7]
    assert out[0] == {
        "txn_id": 9,
        "ts": "2022-09-01T10:00:00",
        "amount": "12.50",
        "currency": "US Dollar",
    }


# ------------------------------------------------------------------ refusals


def test_refuses_non_local_url() -> None:
    with pytest.raises(ex.ExportError, match="non-local"):
        ex.readonly_engine("postgresql+psycopg://u:p@example.neon.tech/db")


def test_refuses_scripted_runs(results_file: Path) -> None:
    for kw in ({"profile": "scripted"}, {"provider": "scripted"}):
        facts = _facts(trace_path=str(results_file), **kw)
        with pytest.raises(ex.ExportError, match="scripted"):
            ex.check_run(facts, "r1", DEV, HELD)


def test_refuses_held_out_and_unknown_alerts(results_file: Path) -> None:
    with pytest.raises(ex.ExportError, match="held out"):
        ex.check_run(_facts(case_id="AL-held", trace_path=str(results_file)), "r1", DEV, HELD)
    with pytest.raises(ex.ExportError, match="not a development alert"):
        ex.check_run(_facts(case_id="AL-other", trace_path=str(results_file)), "r1", DEV, HELD)


def test_refuses_missing_results_file(tmp_path: Path) -> None:
    for path in (None, str(tmp_path / "results.jsonl"), str(tmp_path / "other.jsonl")):
        with pytest.raises(ex.ExportError, match="results.jsonl is missing"):
            ex.check_run(_facts(trace_path=path), "r1", DEV, HELD)


def test_accepts_a_good_run(results_file: Path) -> None:
    assert ex.check_run(_facts(trace_path=str(results_file)), "r1", DEV, HELD) == "v1"
    with pytest.raises(ex.ExportError, match="not found"):
        ex.check_run(None, "r1", DEV, HELD)


# ------------------------------------------------------------------ results


def _strict(tn: int, fn: int) -> dict[str, Any]:
    return {"tn": tn, "fn": fn, "tp": 1, "fp": 2, "n": 9, "precision": 0.3, "recall": 0.5}


def _write_score(tmp_path: Path) -> ex.Sources:
    split = {
        "alerts": 9,
        "baseline": {"false_strict": 7, "true_strict": 2, "raised": 9, "precision_strict": 0.2},
        "agent_with_human_review": {"strict": _strict(4, 1), "loose": {}},
        "human_review": {"count": 2},
    }
    score = {
        "run_id": "eval-1",
        "dataset": {"name": "dev"},
        "model": {"model": "free/model"},
        "prompt": {"version": "v3"},
        "late_cutoff": "2022-09-10",
        "splits": {"early": split, "all": split, "late": split},
    }
    rules = {
        "alert_total": 171,
        "metrics": {
            "overall": {
                "alerts": {
                    "raised": 171,
                    "precision_strict": 0.22,
                    "true_strict": 39,
                    "false_strict": 132,
                }
            }
        },
    }
    (tmp_path / "score.json").write_text(json.dumps(score), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    return ex.Sources(score=tmp_path / "score.json", rules_baseline=tmp_path / "rules.json")


def test_results_counts_and_notes(tmp_path: Path) -> None:
    out = ex.build_results(_write_score(tmp_path))
    assert set(out["splits"]) == {"early", "all"}
    assert out["splits"]["early"]["counts"] == {
        "false_total": 7,
        "real_total": 2,
        "false_closed": 4,
        "real_closed": 1,
    }
    assert "loose" not in out["splits"]["early"]["agent_with_human_review"]
    assert out["rules_171"]["alerts"] == 171 and "population" in out["rules_171"]
    assert len(out["notes"]) == 6
    text = json.dumps(out)
    assert not any(d in text for d in DASHES)


def test_results_refuses_missing_score(tmp_path: Path) -> None:
    with pytest.raises(ex.ExportError, match="score file not found"):
        ex.build_results(ex.Sources(score=tmp_path / "nope.json"))


# ------------------------------------------------------------------ output


def test_dumps_is_stable() -> None:
    a = ex.dumps({"b": 1, "a": ["é", 2]})
    b = ex.dumps({"a": ["é", 2], "b": 1})
    assert a == b and a.endswith(b"\n") and "é".encode() in a


def test_write_bundle_removes_only_stale_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "old.json").write_text("{}", encoding="utf-8")
    (runs / "notes.txt").write_text("keep", encoding="utf-8")
    ex.write_bundle(tmp_path, {"runs/new.json": b"{}\n", "manifest.json": b"{}\n"})
    assert sorted(p.name for p in runs.iterdir()) == ["new.json", "notes.txt"]
    assert (tmp_path / "manifest.json").read_bytes() == b"{}\n"


def test_curated_table_matches_the_plan() -> None:
    ids = [c.run_id for c in ex.CURATED]
    assert len(ids) == 8 == len(set(ids)) and ex.FEATURED == ids[0]
    stories = {s.id: [c.run_id for c in ex.CURATED if c.story == s.id] for s in ex.STORIES}
    assert stories["prompt-versions"] == ids[:4] and stories["other-detectors"] == ids[4:6]
    for c in ex.CURATED:
        assert not any(d in c.caption for d in DASHES)
