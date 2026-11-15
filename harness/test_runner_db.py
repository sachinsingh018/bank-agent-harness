"""run_case against the real AML tools server with a scripted model.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only."""

import asyncio
import json
from typing import Any

from harness.models.scripted_adapter import ScriptedAdapter, ScriptItem, tool_call_response
from harness.models.types import ModelRequest, ModelResponse, ToolResultBlock
from harness.runner import run_case
from harness.trace import CaseResult
from modules.aml.agent.models import SUBMIT_TOOL_NAME, AmlFindings, CitedAmount, Claim, Typology
from modules.aml.agent.spec import AML_SPEC
from tools.aml.query_registry import Samples


def _tool_results(req: ModelRequest) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for message in req.messages:
        for block in message.blocks:
            if isinstance(block, ToolResultBlock) and not block.is_error:
                found.append(json.loads(block.content))
    return found


def _alert(req: ModelRequest) -> dict[str, Any]:
    return next(r for r in _tool_results(req) if "transfers" in r)


def _describe(req: ModelRequest) -> ModelResponse:
    detector = _alert(req)["detector"]
    return tool_call_response([("describe_detector", {"detector": detector})], id_prefix="d")


def _findings(req: ModelRequest, value_suffix: str = "") -> dict[str, Any]:
    """Close findings citing the first alert transfer with its paid amount as the tool
    returned it. value_suffix makes the amount wrong ("1" appends a digit)."""
    alert = _alert(req)
    first = alert["transfers"]["items"][0]
    paid = first["amount_paid"]
    findings = AmlFindings(
        alert_id=alert["alert_id"],
        recommendation="close",
        confidence=0.1,
        typology=Typology.NONE,
        narrative="Scripted test finding.",
        claims=[
            Claim(
                statement="The first alert transfer.",
                txn_ids=[first["txn_id"]],
                amounts=[
                    CitedAmount(
                        txn_id=first["txn_id"],
                        side="paid",
                        value=paid["value"] + value_suffix,
                        currency=paid["currency"],
                    )
                ],
            )
        ],
    )
    return findings.model_dump(mode="json")


def _submit(req: ModelRequest) -> ModelResponse:
    return tool_call_response([(SUBMIT_TOOL_NAME, _findings(req))], id_prefix="s")


def _submit_wrong(prefix: str) -> ScriptItem:
    def item(req: ModelRequest) -> ModelResponse:
        return tool_call_response([(SUBMIT_TOOL_NAME, _findings(req, "1"))], id_prefix=prefix)

    return item


def _run(
    harness_test_url: str, alert_id: str, tail: list[ScriptItem]
) -> tuple[CaseResult, ScriptedAdapter]:
    script: list[ScriptItem] = [
        tool_call_response([("get_alert", {"alert_id": alert_id})], id_prefix="a"),
        _describe,
        *tail,
    ]
    adapter = ScriptedAdapter(script)
    result = asyncio.run(
        run_case(
            alert_id,
            AML_SPEC,
            "scripted",
            database_url=harness_test_url,
            adapter=adapter,
        )
    )
    assert adapter.remaining == 0
    return result, adapter


def test_run_case_with_scripted_model(harness_test_url: str, harness_samples: Samples) -> None:
    result, _ = _run(harness_test_url, harness_samples.alert_id, [_submit])
    assert result.status == "proposed", (result.reason, result.verifier_results)
    assert result.reason is None
    assert [(r.name, r.passed) for r in result.verifier_results] == [
        ("citations", True),
        ("arithmetic", True),
        ("alert_membership", True),
    ]
    assert result.trace.retry_used is False
    assert result.tool_steps == 2
    assert result.cost_usd == "0"
    assert result.findings is not None
    cited = result.findings["claims"][0]["txn_ids"][0]
    assert cited in result.trace.seen_txn_ids
    assert [r.name for r in result.trace.tool_calls] == [
        "get_alert",
        "describe_detector",
        SUBMIT_TOOL_NAME,
    ]


def test_wrong_amount_twice_is_human_review(
    harness_test_url: str, harness_samples: Samples
) -> None:
    tail = [_submit_wrong("s1"), _submit_wrong("s2")]
    result, _ = _run(harness_test_url, harness_samples.alert_id, tail)
    assert result.status == "human_review" and result.reason == "verifier_failed"
    assert result.trace.retry_reason == "verifier_failed"
    citations = result.verifier_results[0]
    assert citations.name == "citations" and not citations.passed
    assert any("does not match the stored amount" in e for e in citations.errors)


def test_wrong_then_corrected_amount_is_proposed(
    harness_test_url: str, harness_samples: Samples
) -> None:
    result, adapter = _run(
        harness_test_url, harness_samples.alert_id, [_submit_wrong("s1"), _submit]
    )
    assert result.status == "proposed", (result.reason, result.verifier_results)
    assert result.trace.retry_used is True
    assert result.trace.retry_reason == "verifier_failed"
    # The model saw the verifier error on the retry, naming the transfer but no stored value.
    last = adapter.requests[-1].messages[-1].blocks[0]
    assert isinstance(last, ToolResultBlock) and last.is_error
    assert "does not match the stored amount" in last.content
