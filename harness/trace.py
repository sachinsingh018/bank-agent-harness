"""The record of one case run: every model call, every tool call and the final result.

Records hold no secrets and no exception messages: a failed model call is logged by its
exception class name only. Tool result content is stored as a sha256 in each ToolCallRecord;
the content itself is kept in RunTrace.tool_results only when the run asks for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.mcp_client import OutcomeCode
from harness.models.types import Message, StopReason, Usage
from harness.verifiers.base import VerifierResult

HumanReviewReason = Literal[
    "limit_steps",
    "limit_tokens",
    "limit_time",
    "limit_output",
    "refusal",
    "model_error",
    "no_answer",
    "schema_failed",
    "verifier_failed",
    "verifier_error",
    "tool_failure",
    "internal_error",
    "reviewer_rejected",  # the checker read the verified findings and rejected them
    "reviewer_failed",  # the checker hit a limit, broke its schema or failed otherwise
]
CaseStatus = Literal["proposed", "human_review"]
RetryReason = Literal["no_answer", "schema_failed", "verifier_failed"]
ReviewVerdict = Literal["approve", "reject"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCallRecord(_Strict):
    index: int = Field(ge=0)
    provider: str
    model: str
    prompt_version: str
    prompt_sha256: str
    usage: Usage
    cost_usd: str  # exact Decimal written as a string
    latency_ms: int = Field(ge=0)
    stop_reason: StopReason
    request_id: str | None


class ToolCallRecord(_Strict):
    index: int = Field(ge=0)
    step: int | None  # None for the submit tool, which is not a step
    name: str
    arguments: dict[str, Any]
    ok: bool
    error_code: OutcomeCode | None
    duration_ms: int = Field(ge=0)
    size: int = Field(ge=0)
    truncated: bool
    repeat: bool
    content_sha256: str
    # Paths of JSON-encoded string arguments unpacked before the call (harness.arg_repair).
    arg_repairs: list[str] = []


class ToolResultContent(_Strict):
    index: int = Field(ge=0)  # matches ToolCallRecord.index
    content: str


class RunTrace(_Strict):
    case_id: str
    module: str
    started_at: datetime
    finished_at: datetime
    model_calls: list[ModelCallRecord] = []
    tool_calls: list[ToolCallRecord] = []
    tool_results: list[ToolResultContent] = []  # empty unless content is kept
    messages: list[Message] = []
    retry_used: bool = False
    retry_reason: RetryReason | None = None
    seen_txn_ids: list[int] = []
    # Paths unpacked from JSON-encoded strings in the last submission (harness.arg_repair).
    submission_repairs: list[str] = []
    # Ids of the lessons added to the first user message (harness.lessons), in shown order.
    lesson_ids: list[int] = []
    # Class name of the exception behind model_error, tool_failure, verifier_error or
    # internal_error. Never the message, which may hold request details.
    error_class: str | None = None
    # Langfuse trace id when the run was traced (harness/tracing.py), else None.
    trace_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.seen_txn_ids != sorted(set(self.seen_txn_ids)):
            raise ValueError("seen_txn_ids must be sorted and distinct")
        if self.retry_used != (self.retry_reason is not None):
            raise ValueError("retry_reason is set exactly when retry_used is true")
        return self


class ReviewOutcome(_Strict):
    """The checker's run on a maker result that passed every verifier (maker-checker).

    verdict is None exactly when the checker failed; failure_reason is then the checker's own
    stop reason. review is the checker's validated output model, dumped to JSON data; the
    harness reads only its verdict. totals, cost and tool steps are the checker's alone."""

    verdict: ReviewVerdict | None
    failure_reason: HumanReviewReason | None
    review: dict[str, Any] | None
    verifier_results: list[VerifierResult]
    profile: str
    provider: str
    model: str
    prompt_version: str
    prompt_sha256: str
    totals: Usage
    cost_usd: str  # exact Decimal written as a string
    tool_steps: int = Field(ge=0)
    wall_ms: int = Field(ge=0)
    trace: RunTrace

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.verdict is None) == (self.failure_reason is None):
            raise ValueError("a review has either a verdict or a failure reason")
        if self.verdict is not None:
            if self.review is None:
                raise ValueError("a review with a verdict needs the review itself")
            if self.review.get("verdict") != self.verdict:
                raise ValueError("the verdict must match the review's verdict field")
        return self


class CaseResult(_Strict):
    case_id: str
    status: CaseStatus
    reason: HumanReviewReason | None
    findings: dict[str, Any] | None  # the validated output model, dumped to JSON data
    verifier_results: list[VerifierResult]
    totals: Usage
    cost_usd: str  # exact Decimal written as a string
    tool_steps: int = Field(ge=0)
    wall_ms: int = Field(ge=0)  # maker plus checker
    trace: RunTrace  # the maker's trace; the checker's is in review.trace
    # totals and cost_usd include the checker; tool_steps is the maker's (see review).
    review: ReviewOutcome | None = None
    # True when the module has a checker and review was not turned off for this run: then a
    # proposed result needs an approving review. Results written before maker-checker have
    # no such field and load as False.
    review_required: bool = False

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        self._review_invariants()
        if self.status == "proposed":
            if self.findings is None:
                raise ValueError("a proposed result needs findings")
            if self.reason is not None:
                raise ValueError("a proposed result has no human review reason")
            if not self.verifier_results:
                raise ValueError("a proposed result needs at least one verifier result")
            if not all(r.passed for r in self.verifier_results):
                raise ValueError("a proposed result needs every verifier to pass")
        elif self.reason is None:
            raise ValueError("a human_review result needs a reason")
        elif self.reason == "verifier_error" and self.trace.error_class is None:
            raise ValueError("a verifier_error result needs the exception class in the trace")
        return self

    def _review_invariants(self) -> None:
        review = self.review
        if self.reason in ("reviewer_rejected", "reviewer_failed"):
            if review is None:
                raise ValueError("a reviewer reason needs the review")
            if self.findings is None or not self.verifier_results:
                raise ValueError("a reviewer reason needs verified findings")
            if not all(r.passed for r in self.verifier_results):
                raise ValueError("only findings that passed every verifier are reviewed")
            wanted = "reject" if self.reason == "reviewer_rejected" else None
            if review.verdict != wanted:
                raise ValueError(f"{self.reason} does not match the review verdict")
            return
        if review is not None and (self.status != "proposed" or review.verdict != "approve"):
            raise ValueError("a review that is not a reject or a failure must approve a proposal")
        if self.status == "proposed" and self.review_required and review is None:
            raise ValueError("a proposed result needs an approving review")
