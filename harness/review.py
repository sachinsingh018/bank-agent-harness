"""Maker-checker: a second agent (the checker) reviews findings that passed the verifiers.

The checker reuses run_agent with the module's ReviewerSpec: its own prompt, output model,
submit tool, verifiers and limits, but the same read-only tool session and lookup as the
maker. The harness reads only the review's verdict field:

    approve -> proposed, with the review attached
    reject  -> human_review reviewer_rejected, with the review attached
    failure -> human_review reviewer_failed (limit, schema, model error, missing verdict)

A rejection or failure never closes anything. The checker writes nowhere, like the maker.
Plan: docs/plans/maker-checker.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from harness.config import HarnessLimits
from harness.loop import LookupFactory, ToolSessionLike, human_review_without_run, run_agent
from harness.models.config import ModelProfile
from harness.models.types import ModelAdapter
from harness.module_spec import VERDICT_FIELD, ModuleSpec, ReviewerSpec
from harness.prompts import load_prompt
from harness.trace import CaseResult, HumanReviewReason, ReviewOutcome, ReviewVerdict

VERDICTS: tuple[ReviewVerdict, ...] = ("approve", "reject")


def checker_spec(spec: ModuleSpec, reviewer: ReviewerSpec, findings: dict[str, Any]) -> ModuleSpec:
    """The module spec as the checker sees it: same tools and lookup, the reviewer's prompt,
    output model, submit tool and verifiers, and a first message showing the findings."""

    def first_message(case_id: str) -> str:
        return reviewer.build_review_message(case_id, findings)

    return replace(
        spec,
        prompt=reviewer.prompt,
        output_model=reviewer.output_model,
        submit_tool=reviewer.submit_tool,
        verifiers=tuple(reviewer.make_verifiers(findings)),
        build_case_message=first_message,
        reviewer=None,
    )


def _verdict(result: CaseResult) -> ReviewVerdict | None:
    value = None if result.findings is None else result.findings.get(VERDICT_FIELD)
    return next((v for v in VERDICTS if v == value), None)


async def run_review(
    *,
    maker: CaseResult,
    spec: ModuleSpec,
    adapter: ModelAdapter,
    profile: ModelProfile,
    tools: ToolSessionLike,
    limits: HarnessLimits,
    verify_context_factory: LookupFactory,
    clock: Callable[[], float] = time.monotonic,
    keep_tool_content: bool = True,
) -> ReviewOutcome:
    """Run the checker on a proposed maker result. Never raises for a checker problem: every
    failure is a ReviewOutcome with a failure_reason."""
    reviewer = spec.reviewer
    if reviewer is None or maker.status != "proposed" or maker.findings is None:
        raise ValueError("only a proposed result of a module with a reviewer is reviewed")
    prompt = load_prompt(reviewer.prompt)
    try:
        cspec = checker_spec(spec, reviewer, maker.findings)
    except Exception as exc:  # a broken reviewer wiring still ends in human review
        result = human_review_without_run(
            maker.case_id, spec.name, "internal_error", error_class=type(exc).__name__
        )
    else:
        result = await run_agent(
            case_id=maker.case_id,
            spec=cspec,
            adapter=adapter,
            profile=profile,
            tools=tools,
            limits=limits,
            prompt=prompt,
            verify_context_factory=verify_context_factory,
            clock=clock,
            keep_tool_content=keep_tool_content,
        )
    verdict = _verdict(result) if result.status == "proposed" else None
    failure: HumanReviewReason | None = None
    if verdict is None:
        failure = result.reason if result.reason is not None else "internal_error"
    return ReviewOutcome(
        verdict=verdict,
        failure_reason=failure,
        review=result.findings if verdict is not None else None,
        verifier_results=result.verifier_results,
        profile=profile.name,
        provider=profile.provider,
        model=profile.model,
        prompt_version=prompt.version,
        prompt_sha256=prompt.sha256,
        totals=result.totals,
        cost_usd=result.cost_usd,
        tool_steps=result.tool_steps,
        wall_ms=result.wall_ms,
        trace=result.trace,
    )


def apply_review(maker: CaseResult, review: ReviewOutcome | None, required: bool) -> CaseResult:
    """The case result after review. Totals, cost and wall time include the checker; the
    findings and verifier results stay the maker's."""
    if review is None:
        return CaseResult(**_fields(maker), review=None, review_required=required)
    fields = _fields(maker)
    fields["totals"] = maker.totals + review.totals
    fields["cost_usd"] = str(Decimal(maker.cost_usd) + Decimal(review.cost_usd))
    fields["wall_ms"] = maker.wall_ms + review.wall_ms
    if review.verdict == "approve":
        fields["status"], fields["reason"] = "proposed", None
    else:
        fields["status"] = "human_review"
        fields["reason"] = "reviewer_rejected" if review.verdict == "reject" else "reviewer_failed"
    return CaseResult(**fields, review=review, review_required=required)


def _fields(result: CaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "status": result.status,
        "reason": result.reason,
        "findings": result.findings,
        "verifier_results": result.verifier_results,
        "totals": result.totals,
        "cost_usd": result.cost_usd,
        "tool_steps": result.tool_steps,
        "wall_ms": result.wall_ms,
        "trace": result.trace,
    }
