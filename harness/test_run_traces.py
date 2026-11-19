"""Pure tests for the stored trace form: capping, the case card and the stored model."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness.models.types import Message, TextBlock, ToolCallBlock, ToolResultBlock, Usage
from harness.run_traces import (
    CAPPED_KEY,
    CUT_MARK,
    cap_content,
    cap_trace,
    case_card,
    first_tool_result,
    stored_trace,
)
from harness.trace import CaseResult, RunTrace, ToolResultContent


def _big_alert(n: int) -> str:
    items = [
        {
            "txn_id": i,
            "from_account": {"bank": 1, "acct": "A"},
            "to_account": {"bank": 2, "acct": f"B{i}"},
            "note": "x" * 50,
        }
        for i in range(n)
    ]
    return json.dumps(
        {"alert_id": "AL-000000000001", "detector": "fan-out", "transfers": {"items": items}}
    )


def test_short_content_is_unchanged() -> None:
    assert cap_content("hello", 10) == "hello"


def test_long_json_stays_valid_json_with_a_note() -> None:
    capped = cap_content(_big_alert(300), 4000)
    assert len(capped) <= 4000
    data = json.loads(capped)
    assert data["detector"] == "fan-out"
    kept = data["transfers"]["items"]
    assert 0 < len(kept) < 300
    assert [t["txn_id"] for t in kept] == list(range(len(kept)))  # the first items stay
    assert data[CAPPED_KEY] == f"{300 - len(kept)} list items left out"


def test_long_text_is_cut_with_a_mark() -> None:
    capped = cap_content("y" * 500, 100)
    assert len(capped) == 100 and capped.endswith(CUT_MARK)


def test_json_without_lists_falls_back_to_a_cut() -> None:
    capped = cap_content(json.dumps({"a": "z" * 500}), 100)
    assert capped.endswith(CUT_MARK)


def _trace(content: str) -> RunTrace:
    now = datetime.now(UTC)
    return RunTrace(
        case_id="AL-000000000001",
        module="aml",
        started_at=now,
        finished_at=now,
        messages=[
            Message(role="user", blocks=[TextBlock(text="Investigate.")]),
            Message(
                role="assistant",
                blocks=[ToolCallBlock(id="c1", name="get_alert", arguments={})],
                provider_raw={"big": "raw"},
                provider="scripted",
            ),
            Message(role="user", blocks=[ToolResultBlock(tool_call_id="c1", content=content)]),
        ],
        tool_results=[ToolResultContent(index=0, content=content)],
    )


def test_cap_trace_caps_messages_and_results_and_drops_provider_raw() -> None:
    trace = cap_trace(_trace(_big_alert(300)), 3000)
    result = trace.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock) and len(result.content) <= 3000
    assert len(trace.tool_results[0].content) <= 3000
    assert trace.messages[1].provider_raw is None


def test_case_card_reads_the_first_alert_result() -> None:
    trace = _trace(_big_alert(3))
    assert first_tool_result(trace, "get_alert") is not None
    card = case_card(trace)
    assert card is not None and card["alert_id"] == "AL-000000000001"
    assert case_card(_trace(json.dumps({"code": "not_found", "message": "no"}))) is None
    assert case_card(trace.model_copy(update={"module": "kyb"})) is None


def test_stored_trace_has_maker_and_card() -> None:
    trace = _trace(_big_alert(3))
    result = CaseResult(
        case_id=trace.case_id,
        status="human_review",
        reason="limit_steps",
        findings=None,
        verifier_results=[],
        totals=Usage(input_tokens=1, output_tokens=1),
        cost_usd="0",
        tool_steps=1,
        wall_ms=1,
        trace=trace,
    )
    stored, card = stored_trace(result)
    assert stored.checker is None and stored.maker.case_id == trace.case_id
    assert card is not None and card["detector"] == "fan-out"
