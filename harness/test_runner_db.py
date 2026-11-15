"""run_case against the real AML tools server with a scripted model.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only."""

import asyncio
import json
from typing import Any

from harness.models.scripted_adapter import ScriptedAdapter, ScriptItem, tool_call_response
from harness.models.types import ModelRequest, ModelResponse, ToolResultBlock
from harness.runner import run_case
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


def _submit(req: ModelRequest) -> ModelResponse:
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
                        value=paid["value"],
                        currency=paid["currency"],
                    )
                ],
            )
        ],
    )
    args = findings.model_dump(mode="json")
    return tool_call_response([(SUBMIT_TOOL_NAME, args)], id_prefix="s")


def test_run_case_with_scripted_model(harness_test_url: str, harness_samples: Samples) -> None:
    script: list[ScriptItem] = [
        tool_call_response([("get_alert", {"alert_id": harness_samples.alert_id})], id_prefix="a"),
        _describe,
        _submit,
    ]
    adapter = ScriptedAdapter(script)
    result = asyncio.run(
        run_case(
            harness_samples.alert_id,
            AML_SPEC,
            "scripted",
            database_url=harness_test_url,
            adapter=adapter,
        )
    )
    # AML_SPEC has no verifiers until step 5, and unverified output is never proposed.
    # Step 5 adds the verifiers and flips this to status "proposed" with reason None.
    assert result.status == "human_review", (result.reason, result.trace.error_class)
    assert result.reason == "internal_error"
    assert result.verifier_results == []
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
    assert adapter.remaining == 0
