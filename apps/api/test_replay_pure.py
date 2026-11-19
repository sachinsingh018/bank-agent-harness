"""Pure tests for the replay steps and the money-flow graph. No database, no model calls."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from apps.api.replay import (
    MAX_NODES,
    GraphBuilder,
    build_replay,
    card_graph,
    layout,
    result_summary,
)
from harness.models.types import Message, TextBlock, ToolCallBlock, ToolResultBlock
from harness.run_traces import StoredTrace
from harness.trace import RunTrace

ALERT_ID = "AL-0000000000aa"
ANCHOR = {"bank": 1, "acct": "A"}


def _transfer(txn_id: int, src: dict[str, Any], dst: dict[str, Any]) -> dict[str, Any]:
    return {
        "txn_id": txn_id,
        "ts": "2022-09-01T10:00:00",
        "from_account": src,
        "to_account": dst,
        "amount_paid": {"value": "100.000000", "currency": "US Dollar"},
        "amount_received": {"value": "100.000000", "currency": "US Dollar"},
        "format": "ACH",
    }


ALERT = {
    "alert_id": ALERT_ID,
    "ruleset_version": "v1",
    "detector": "fan-out",
    "anchor": ANCHOR,
    "window_start": "2022-09-01T00:00:00",
    "window_end": "2022-09-02T00:00:00",
    "txn_count": 3,
    "account_count": 4,
    "params": {},
    "summary": "Account 1/A sent to 3 different accounts in 3 transfers.",
    "transfers": {
        "items": [_transfer(10 + i, ANCHOR, {"bank": 2, "acct": f"B{i}"}) for i in range(3)],
        "total_count": 3,
        "returned": 3,
        "truncated": False,
    },
}
COUNTERPARTIES = {
    "account": ANCHOR,
    "items": [
        {
            "account": {"bank": 3, "acct": "C"},
            "transfers": 4,
            "first_seen": "2022-09-01T00:00:00",
            "last_seen": "2022-09-03T00:00:00",
            "sent_transfers": 0,
            "received_transfers": 4,
            "sent_totals_by_currency": [],
            "received_totals_by_currency": [],
        }
    ],
    "total_count": 1,
    "returned": 1,
    "truncated": False,
}
FINDINGS = {
    "alert_id": ALERT_ID,
    "recommendation": "escalate",
    "confidence": 0.8,
    "typology": "FAN-OUT",
    "narrative": "Money fanned out.",
    "claims": [{"statement": "Three payouts.", "txn_ids": [10, 11, 12], "amounts": []}],
    "figures": [],
}


def _call(call_id: str, name: str, args: dict[str, Any]) -> Message:
    return Message(role="assistant", blocks=[ToolCallBlock(id=call_id, name=name, arguments=args)])


def _result(call_id: str, content: str, is_error: bool = False) -> Message:
    return Message(
        role="user",
        blocks=[ToolResultBlock(tool_call_id=call_id, content=content, is_error=is_error)],
    )


def _maker() -> RunTrace:
    now = datetime.now(UTC)
    error = json.dumps({"code": "invalid_submission", "errors": ["arithmetic: total is off"]})
    return RunTrace(
        case_id=ALERT_ID,
        module="aml",
        started_at=now,
        finished_at=now,
        retry_used=True,
        retry_reason="verifier_failed",
        lesson_ids=[4],
        messages=[
            Message(role="user", blocks=[TextBlock(text="Please investigate.")]),
            _call("c1", "get_alert", {"alert_id": ALERT_ID}),
            _result("c1", json.dumps(ALERT)),
            Message(
                role="assistant",
                blocks=[
                    TextBlock(text="Next I check who pays the anchor."),
                    ToolCallBlock(
                        id="c2",
                        name="get_counterparties",
                        arguments={"bank": 1, "acct": "A", "direction": "in"},
                    ),
                ],
            ),
            _result("c2", json.dumps(COUNTERPARTIES)),
            _call("s1", "submit_findings", FINDINGS),
            _result("s1", error, is_error=True),
            _call("c3", "get_transaction", {"txn_id": 999}),
            _result("c3", json.dumps({"code": "not_found", "message": "no such transfer"}), True),
            _call("s2", "submit_findings", FINDINGS),
        ],
    )


def _checker() -> RunTrace:
    now = datetime.now(UTC)
    return RunTrace(case_id=ALERT_ID, module="aml", started_at=now, finished_at=now)


def _replay() -> tuple[Any, Any, Any]:
    run: dict[str, Any] = {
        "case_id": ALERT_ID,
        "status": "proposed",
        "reason": None,
        "findings": FINDINGS,
        "verifier_results": [
            {"name": "citations", "passed": True, "errors": []},
            {"name": "arithmetic", "passed": True, "errors": []},
        ],
    }
    review: dict[str, Any] = {
        "verdict": "approve",
        "failure_reason": None,
        "review": {"verdict": "approve", "reasons": ["Grounded."], "concerns": []},
        "verifier_results": [],
    }
    stored = StoredTrace(tool_result_cap=12_000, maker=_maker(), checker=_checker())
    return build_replay(run, stored, ALERT, review)


def test_steps_follow_the_run_with_a_retry_and_a_review() -> None:
    alert, steps, _ = _replay()
    assert alert.detector == "fan-out" and alert.anchor == "1/A"
    kinds = [s.kind for s in steps]
    assert kinds == [
        "alert",
        "tool_call",
        "tool_result",
        "thinking",
        "tool_call",
        "tool_result",
        "tool_call",
        "retry",
        "tool_call",
        "tool_result",
        "tool_call",
        "checks",
        "review",
        "outcome",
    ]
    assert [s.index for s in steps] == list(range(len(steps)))
    assert "1 saved lesson" in steps[0].explanation
    assert steps[4].explanation == "Asked who pays account 1/A."
    assert steps[5].summary == "1 counterparty; the busiest is 3/C with 4 transfers."
    assert steps[7].errors == ["arithmetic: total is off"]
    assert steps[9].ok is False and steps[9].title == "The tool refused"
    assert steps[10].claims[0].txn_ids == [10, 11, 12]
    assert steps[11].ok is True and len(steps[11].checks) == 2
    assert steps[12].verdict == "approve" and steps[12].reasons == ["Grounded."]
    outcome = steps[-1]
    assert outcome.status == "proposed" and outcome.recommendation == "escalate"
    assert outcome.txn_ids == [10, 11, 12]
    for step in steps:
        assert step.explanation and step.title
        text = step.explanation + step.title + (step.summary or "")
        assert "\u2014" not in text and "\u2013" not in text


def test_graph_grows_with_the_steps() -> None:
    _, steps, graph = _replay()
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["1/A"].role == "anchor" and nodes["1/A"].step == 0
    assert nodes["2/B0"].role == "alert"
    assert nodes["3/C"].role == "found" and nodes["3/C"].step == 5
    edges = {e.id: e for e in graph.edges}
    assert edges["1/A>2/B0"].txn_ids == [10]
    assert edges["3/C>1/A"].count == 4 and edges["3/C>1/A"].step == 5
    assert steps[0].txn_ids == [10, 11, 12]
    for n in graph.nodes:
        assert -100 <= n.x <= 100 and -100 <= n.y <= 100


def test_layout_is_deterministic_and_spreads_nodes() -> None:
    core = ["a", "b", "c", "d"]
    extras = [f"x{i}" for i in range(30)]
    edges = [("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")]
    parent = {e: "b" for e in extras}
    first = layout(core, extras, edges, "a", "cycle", parent)
    assert first == layout(core, extras, edges, "a", "cycle", parent)
    points = list(first.values())
    for i, p in enumerate(points):
        for q in points[i + 1 :]:
            assert math.dist(p, q) > 5


def test_layered_core_for_scatter_gather() -> None:
    core = ["s", "m1", "m2", "t"]
    edges = [("s", "m1"), ("s", "m2"), ("m1", "t"), ("m2", "t")]
    pos = layout(core, [], edges, "s", "scatter-gather", {})
    assert pos["s"][0] < pos["m1"][0] == pos["m2"][0] < pos["t"][0]


def test_node_cap_counts_hidden_accounts() -> None:
    builder = GraphBuilder()
    for i in range(MAX_NODES + 5):
        builder.add_link("1/R", f"9/N{i}", i, 1, 1)
    graph = builder.graph("1/R", None)
    assert len(graph.nodes) == MAX_NODES and graph.hidden_accounts == 6


def test_card_graph_and_summaries_never_raise() -> None:
    graph = card_graph(ALERT)
    assert len(graph.nodes) == 4 and len(graph.edges) == 3
    assert card_graph(None).nodes == []
    assert result_summary("get_account", "not json", True) == "The tool answered with text."
    assert result_summary("sum_transfers", json.dumps({"totals": "bad"}), True)
