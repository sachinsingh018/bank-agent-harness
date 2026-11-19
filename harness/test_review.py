"""Maker-checker without a database: apply_review, the CaseResult review invariants, the
reviewer limits and the reviewer spec check."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from harness.config import HarnessLimits, load_limits, load_reviewer_limits
from harness.models.types import ToolSpec, Usage
from harness.module_spec import ReviewerSpec
from harness.prompts import PromptRef
from harness.review import apply_review
from harness.trace import CaseResult, ReviewOutcome, RunTrace
from harness.verifiers.base import VerifierResult

NOW = datetime(2026, 1, 2, tzinfo=UTC)
PASSED = [VerifierResult(name="citations", passed=True, errors=[])]


def _trace() -> RunTrace:
    return RunTrace(case_id="C-1", module="aml", started_at=NOW, finished_at=NOW)


def _maker(**extra: Any) -> CaseResult:
    fields: dict[str, Any] = {
        "case_id": "C-1",
        "status": "proposed",
        "reason": None,
        "findings": {"recommendation": "close", "claims": [{}]},
        "verifier_results": PASSED,
        "totals": Usage(input_tokens=100, output_tokens=10),
        "cost_usd": "0.10",
        "tool_steps": 3,
        "wall_ms": 1000,
        "trace": _trace(),
    }
    return CaseResult(**(fields | extra))


def _review(verdict: str | None, failure: str | None = None) -> ReviewOutcome:
    return ReviewOutcome(
        verdict=verdict,  # type: ignore[arg-type]
        failure_reason=failure,  # type: ignore[arg-type]
        review=None if verdict is None else {"verdict": verdict, "reasons": ["r"]},
        verifier_results=[],
        profile="scripted",
        provider="scripted",
        model="scripted",
        prompt_version="v1",
        prompt_sha256="0" * 64,
        totals=Usage(input_tokens=50, output_tokens=5),
        cost_usd="0.05",
        tool_steps=1,
        wall_ms=200,
        trace=_trace(),
    )


def test_approve_keeps_the_proposal_and_adds_the_checker_cost() -> None:
    result = apply_review(_maker(), _review("approve"), required=True)
    assert result.status == "proposed" and result.reason is None
    assert result.totals.input_tokens == 150 and result.cost_usd == "0.15"
    assert result.wall_ms == 1200 and result.tool_steps == 3
    assert result.review is not None and result.review_required


def test_reject_is_human_review_with_the_findings_kept() -> None:
    result = apply_review(_maker(), _review("reject"), required=True)
    assert result.status == "human_review" and result.reason == "reviewer_rejected"
    assert result.findings is not None


def test_checker_failure_is_reviewer_failed() -> None:
    result = apply_review(_maker(), _review(None, "limit_steps"), required=True)
    assert result.status == "human_review" and result.reason == "reviewer_failed"


def test_proposed_without_a_required_review_is_refused() -> None:
    with pytest.raises(ValidationError, match="approving review"):
        apply_review(_maker(), None, required=True)
    assert apply_review(_maker(), None, required=False).status == "proposed"


def test_human_review_without_review_is_fine_when_required() -> None:
    maker = _maker(status="human_review", reason="no_answer", findings=None, verifier_results=[])
    result = apply_review(maker, None, required=True)
    assert result.reason == "no_answer" and result.review is None


@pytest.mark.parametrize(
    ("status", "reason", "review"),
    [
        ("human_review", "reviewer_rejected", None),
        ("human_review", "reviewer_rejected", "approve"),
        ("human_review", "reviewer_failed", "reject"),
        ("proposed", None, "reject"),
        ("human_review", "limit_steps", "approve"),
    ],
)
def test_review_invariants(status: str, reason: str | None, review: str | None) -> None:
    outcome = None if review is None else _review(review)
    with pytest.raises(ValidationError):
        _maker(status=status, reason=reason, review=outcome)


def test_review_outcome_checks() -> None:
    with pytest.raises(ValidationError, match="either a verdict or a failure"):
        _review("approve", "limit_steps")
    with pytest.raises(ValidationError, match="either a verdict or a failure"):
        _review(None)
    good = _review("approve")
    with pytest.raises(ValidationError, match="must match"):
        ReviewOutcome.model_validate(good.model_dump() | {"review": {"verdict": "reject"}})


def test_a_result_written_before_maker_checker_still_loads() -> None:
    old = json.loads(_maker().model_dump_json())
    del old["review"], old["review_required"]
    loaded = CaseResult.model_validate(old)
    assert loaded.review is None and loaded.review_required is False


def test_reviewer_limits() -> None:
    reviewer = load_reviewer_limits()
    assert reviewer.max_tool_steps == 3
    assert reviewer.max_total_tokens < load_limits().max_total_tokens
    assert reviewer.max_wall_seconds == load_limits().max_wall_seconds


def test_reviewer_limits_fall_back_to_the_maker_limits(tmp_path: Path) -> None:
    path = tmp_path / "harness.toml"
    path.write_text("[limits]\nmax_tool_steps = 3\n")
    assert load_reviewer_limits(path) == HarnessLimits(max_tool_steps=3)


def test_reviewer_spec_needs_a_verdict_field(tmp_path: Path) -> None:
    class NoVerdict(BaseModel):
        reasons: list[str]

    with pytest.raises(ValueError, match="verdict field"):
        ReviewerSpec(
            prompt=PromptRef(tmp_path / "r_v1.md", "v1"),
            output_model=NoVerdict,
            submit_tool=ToolSpec(name="submit_review", description="d", input_schema={}),
            build_review_message=lambda case_id, findings: case_id,
            make_verifiers=lambda findings: [],
        )
