"""run_case against the real AML tools server with a scripted model, maker and checker.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only."""

import asyncio
import json
from typing import Any

from sqlalchemy import Engine, select

from harness.config import HarnessLimits
from harness.db.records_models import CaseEvent
from harness.lessons import Lesson
from harness.models.config import get_profile
from harness.models.scripted_adapter import (
    ScriptedAdapter,
    ScriptItem,
    text_response,
    tool_call_response,
)
from harness.models.types import ModelRequest, ModelResponse, TextBlock, ToolResultBlock
from harness.prompts import load_prompt
from harness.records import record_case_result, verify_chain
from harness.runner import run_case
from harness.trace import CaseResult
from modules.aml.agent.models import (
    REVIEW_TOOL_NAME,
    SUBMIT_TOOL_NAME,
    AmlFindings,
    CitedAmount,
    Claim,
    Typology,
)
from modules.aml.agent.spec import AML_SPEC, build_case_message
from tests_support.records_db import records_cleaned
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


def _review(verdict: str, claim_index: int | None = None) -> ModelResponse:
    concerns = [] if claim_index is None else [{"claim_index": claim_index, "issue": "Weak."}]
    review = {
        "verdict": verdict,
        "reasons": [f"Scripted {verdict}."],
        "concerns": concerns,
        "confidence": 0.7,
    }
    return tool_call_response([(REVIEW_TOOL_NAME, review)], id_prefix="r")


APPROVE = _review("approve")


def _run(
    harness_test_url: str,
    alert_id: str,
    tail: list[ScriptItem],
    *,
    review: bool = True,
    reviewer_limits: HarnessLimits | None = None,
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
            review=review,
            reviewer_limits=reviewer_limits,
        )
    )
    assert adapter.remaining == 0
    return result, adapter


def test_run_case_with_scripted_model(harness_test_url: str, harness_samples: Samples) -> None:
    result, adapter = _run(harness_test_url, harness_samples.alert_id, [_submit, APPROVE])
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
    # The checker ran with its own prompt and the review tool, and saw the findings.
    assert result.review is not None and result.review.verdict == "approve"
    assert result.review_required is True
    checker_request = adapter.requests[-1]
    assert REVIEW_TOOL_NAME in [t.name for t in checker_request.tools]
    assert SUBMIT_TOOL_NAME not in [t.name for t in checker_request.tools]
    first = checker_request.messages[0].blocks[0]
    assert isinstance(first, TextBlock) and harness_samples.alert_id in first.text
    assert "Scripted test finding." in first.text
    assert result.review.prompt_version == "v2"
    assert [r.name for r in result.review.verifier_results] == ["review_claim_index"]
    assert len(result.trace.model_calls) == 3 and len(result.review.trace.model_calls) == 1
    assert result.totals.input_tokens == 400  # maker 3 calls plus checker 1, 100 each


def test_checker_reject_is_human_review(harness_test_url: str, harness_samples: Samples) -> None:
    tail: list[ScriptItem] = [_submit, _review("reject", claim_index=0)]
    result, _ = _run(harness_test_url, harness_samples.alert_id, tail)
    assert result.status == "human_review" and result.reason == "reviewer_rejected"
    assert result.findings is not None  # the maker's findings stay for the human
    assert result.review is not None and result.review.verdict == "reject"
    assert result.review.review is not None
    assert result.review.review["concerns"] == [{"claim_index": 0, "issue": "Weak."}]


def test_checker_bad_claim_index_twice_is_reviewer_failed(
    harness_test_url: str, harness_samples: Samples
) -> None:
    tail: list[ScriptItem] = [
        _submit,
        _review("reject", claim_index=5),
        _review("reject", claim_index=5),
    ]
    result, _ = _run(harness_test_url, harness_samples.alert_id, tail)
    assert result.status == "human_review" and result.reason == "reviewer_failed"
    assert result.review is not None and result.review.verdict is None
    assert result.review.failure_reason == "verifier_failed"
    assert result.review.verifier_results[0].passed is False


def test_checker_schema_failure_twice_is_reviewer_failed(
    harness_test_url: str, harness_samples: Samples
) -> None:
    bad = tool_call_response([(REVIEW_TOOL_NAME, {"verdict": "maybe"})], id_prefix="r")
    result, _ = _run(harness_test_url, harness_samples.alert_id, [_submit, bad, bad])
    assert result.status == "human_review" and result.reason == "reviewer_failed"
    assert result.review is not None and result.review.failure_reason == "schema_failed"


def test_checker_step_limit_is_reviewer_failed(
    harness_test_url: str, harness_samples: Samples
) -> None:
    look = tool_call_response(
        [("get_alert", {"alert_id": harness_samples.alert_id})] * 2, id_prefix="c"
    )
    result, _ = _run(
        harness_test_url,
        harness_samples.alert_id,
        [_submit, look],
        reviewer_limits=HarnessLimits(max_tool_steps=1),
    )
    assert result.status == "human_review" and result.reason == "reviewer_failed"
    assert result.review is not None and result.review.failure_reason == "limit_steps"


def test_maker_not_proposed_has_no_checker_run(
    harness_test_url: str, harness_samples: Samples
) -> None:
    tail: list[ScriptItem] = [text_response("thinking"), text_response("still thinking")]
    result, _ = _run(harness_test_url, harness_samples.alert_id, tail)
    assert result.status == "human_review" and result.reason == "no_answer"
    assert result.review is None and result.review_required is True


def test_no_review_skips_the_checker(harness_test_url: str, harness_samples: Samples) -> None:
    result, _ = _run(harness_test_url, harness_samples.alert_id, [_submit], review=False)
    assert result.status == "proposed"
    assert result.review is None and result.review_required is False


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
        harness_test_url, harness_samples.alert_id, [_submit_wrong("s1"), _submit], review=False
    )
    assert result.status == "proposed", (result.reason, result.verifier_results)
    assert result.trace.retry_used is True
    assert result.trace.retry_reason == "verifier_failed"
    # The model saw the verifier error on the retry, naming the transfer but no stored value.
    last = adapter.requests[-1].messages[-1].blocks[0]
    assert isinstance(last, ToolResultBlock) and last.is_error
    assert "does not match the stored amount" in last.content


def test_lessons_reach_the_maker_only_and_are_recorded(
    harness_test_url: str, harness_samples: Samples, harness_engine: Engine
) -> None:
    """--lessons on: the lessons block from lessons_block_v1.md is in the maker's first message,
    not in the checker's; the trace and the case event carry the ids and the run digest still
    verifies. Without lessons nothing changes."""
    lesson = Lesson(id=41, text="Regular payroll to many staff accounts is not fan-out.")
    asked: list[str] = []

    def provider(case_id: str) -> list[Lesson]:
        asked.append(case_id)
        return [lesson]

    alert_id = harness_samples.alert_id
    script: list[ScriptItem] = [
        tool_call_response([("get_alert", {"alert_id": alert_id})], id_prefix="a"),
        _describe,
        _submit,
        APPROVE,
    ]
    adapter = ScriptedAdapter(script)
    result = asyncio.run(
        run_case(
            alert_id,
            AML_SPEC,
            "scripted",
            database_url=harness_test_url,
            adapter=adapter,
            lessons_provider=provider,
        )
    )
    assert asked == [alert_id]
    assert result.status == "proposed", result.reason
    assert result.trace.lesson_ids == [41]
    first = adapter.requests[0].messages[0].text()
    assert first.startswith(build_case_message(alert_id))
    assert "Lessons from past reviews" in first and f"- {lesson.text}" in first
    checker_first = adapter.requests[-1].messages[0].text()
    assert lesson.text not in checker_first
    assert result.review is not None and result.review.trace.lesson_ids == []

    plain, plain_adapter = _run(harness_test_url, alert_id, [_submit, APPROVE])
    assert plain.trace.lesson_ids == []
    assert plain_adapter.requests[0].messages[0].text() == build_case_message(alert_id)

    profile = get_profile("scripted")
    prompt = load_prompt(AML_SPEC.prompt)
    with records_cleaned(harness_engine):
        with harness_engine.connect() as conn:
            with_id = record_case_result(conn, result, profile=profile, prompt=prompt)
            plain_id = record_case_result(conn, plain, profile=profile, prompt=prompt)
        with harness_engine.connect() as conn:
            payloads: dict[str, Any] = dict(
                conn.execute(
                    select(CaseEvent.run_id, CaseEvent.payload).where(
                        CaseEvent.run_id.in_([with_id, plain_id])
                    )
                ).all()
            )
            assert payloads[with_id]["lesson_ids"] == [41]
            assert "lesson_ids" not in payloads[plain_id]
            report = verify_chain(conn, check_runs=True)
            assert report.ok, report
