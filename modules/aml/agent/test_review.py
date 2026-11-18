"""The AML checker: AmlReview, the claim index verifier, the review message and the wiring."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from harness.prompts import load_prompt
from harness.verifiers.base import VerifyContext
from modules.aml.agent.models import REVIEW_TOOL_NAME, AmlReview
from modules.aml.agent.spec import AML_SPEC, build_review_message
from modules.aml.agent.verifiers import ReviewClaimIndexVerifier

FINDINGS: dict[str, Any] = {
    "alert_id": "AL-0123456789ab",
    "recommendation": "close",
    "claims": [{"statement": "a"}, {"statement": "b"}],
}
CTX = VerifyContext(case_id="AL-0123456789ab", seen_txn_ids=frozenset(), lookup=None)


def _review(**extra: Any) -> AmlReview:
    body: dict[str, Any] = {"verdict": "reject", "reasons": ["Not supported."], "confidence": 0.7}
    return AmlReview.model_validate(body | extra)


def test_review_model_limits() -> None:
    assert _review().concerns == []
    bad: dict[str, Any]
    for bad in (
        {"verdict": "maybe"},
        {"reasons": []},
        {"reasons": ["r"] * 6},
        {"concerns": [{"issue": "x"}] * 6},
        {"concerns": [{"claim_index": -1, "issue": "x"}]},
        {"confidence": 1.5},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            _review(**bad)


def test_claim_index_verifier() -> None:
    verifier = ReviewClaimIndexVerifier(FINDINGS)
    ok = _review(concerns=[{"claim_index": 1, "issue": "x"}, {"issue": "whole finding"}])
    assert verifier.check(ok, CTX).passed
    bad = verifier.check(_review(concerns=[{"claim_index": 2, "issue": "x"}]), CTX)
    assert not bad.passed and "claim_index 2 does not exist" in bad.errors[0]


def test_review_message_shows_the_alert_and_the_findings() -> None:
    message = build_review_message("AL-0123456789ab", FINDINGS)
    assert "AL-0123456789ab" in message and '"recommendation": "close"' in message
    assert "{alert_id}" not in message and "{findings_json}" not in message
    assert REVIEW_TOOL_NAME in message


def test_reviewer_wiring() -> None:
    reviewer = AML_SPEC.reviewer
    assert reviewer is not None
    assert reviewer.output_model is AmlReview
    assert reviewer.submit_tool.name == REVIEW_TOOL_NAME
    assert [v.name for v in reviewer.make_verifiers(FINDINGS)] == ["review_claim_index"]
    prompt = load_prompt(reviewer.prompt)
    assert prompt.version == "v1" and REVIEW_TOOL_NAME in prompt.text
    assert "at most 6 tool calls" in prompt.text
