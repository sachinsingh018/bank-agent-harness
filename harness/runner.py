"""CaseRunner: set up one case run (limits, model, prompt, tools server) and run the loop.

The runner itself writes nothing and imports no database code. Recording is opt in: a caller
(the eval runner) may pass a recorder callable that writes the finished CaseResult through
the separate write connection in harness/records.py, which the agent never sees.

Maker-checker: when the module has a reviewer and review is on, a proposed maker result goes to
the checker in the same tool session (harness/review.py). The checker uses the maker's profile
unless reviewer_profile_name is given, and the [reviewer] limits.

Lessons (opt in): the caller may pass a lessons provider (case id -> lessons). The runner,
which sits outside the agent, calls it once per case before the loop starts and hands the
lessons to the maker only; the checker never sees them. The provider holds its own read-only
connection; the runner still imports no database code.

Tracing (opt in, harness/tracing.py): the caller may pass a tracer. Each case is one trace,
started here once the prompt is loaded and ended here with the final outcome (after review).
An eval run (eval_run_id given) traces every case; a routine run traces a deterministic
sample ([tracing] sample_routine, hashed from the case id and run id). Tracing never changes
the outcome.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Protocol, runtime_checkable

from harness.config import HarnessLimits, load_limits, load_reviewer_limits, load_tracing
from harness.lessons import Lesson, LessonsProvider
from harness.loop import LookupFactory, human_review_without_run, run_agent
from harness.mcp_client import ToolSession
from harness.models.config import ModelProfile, get_profile
from harness.models.factory import make_adapter
from harness.models.types import ModelAdapter
from harness.module_spec import ModuleSpec
from harness.prompts import load_prompt
from harness.review import apply_review, run_review
from harness.trace import CaseResult, ReviewOutcome
from harness.tracing import (
    NOOP_TRACER,
    NoopTracer,
    RunEnd,
    RunTracer,
    TraceMeta,
    Tracer,
    safe_tracer,
    sampled,
)


@runtime_checkable
class SupportsClose(Protocol):
    """A lookup factory that holds resources (for example a database engine)."""

    def close(self) -> None: ...


async def run_case(
    case_id: str,
    spec: ModuleSpec,
    profile_name: str,
    *,
    database_url: str,
    limits: HarnessLimits | None = None,
    adapter: ModelAdapter | None = None,
    lookup_factory: LookupFactory | None = None,
    keep_tool_content: bool = True,
    recorder: Callable[[CaseResult], str] | None = None,
    review: bool = True,
    reviewer_profile_name: str | None = None,
    reviewer_adapter: ModelAdapter | None = None,
    reviewer_limits: HarnessLimits | None = None,
    lessons_provider: LessonsProvider | None = None,
    tracer: Tracer = NOOP_TRACER,
    eval_run_id: str | None = None,
    dataset: str | None = None,
    run_id: str | None = None,
    sample_routine: float | None = None,
) -> CaseResult:
    """Run one case end to end. A tools server that fails to start is human_review
    tool_failure with an empty trace. Configuration errors (unknown profile, missing prompt
    file) raise, because they are setup mistakes, not case outcomes.

    database_url is passed explicitly to the tools server and to the module's lookup factory
    (DATABASE_URL in normal runs, TEST_DATABASE_URL in tests). Without a lookup_factory, the
    module builds one from database_url and it is closed when the case ends; a factory passed
    in belongs to the caller, who closes it.

    review=False turns the checker off (ablations). The checker's adapter is reviewer_adapter,
    else the maker's adapter when both use the same profile (a scripted adapter then simply
    continues its script), else a new adapter for the reviewer profile.

    lessons_provider, when given, is asked for this case's lessons before the loop starts. A
    provider that raises ends the case as human_review internal_error with the exception class
    in the trace (the case is never run without the lessons it was meant to get).

    tracer (default: nothing is traced) records the case as one trace whose id goes on the
    result's trace. With eval_run_id every case is traced and labelled with it and dataset.
    Without it the case is traced only when sampled(case_id, run_id, rate) holds, where rate
    is sample_routine (default: [tracing] sample_routine) and run_id defaults to a random id.
    A routine traced case is flushed when it ends; an eval run flushes once at its end.

    recorder, when given, is called once with the finished result (every outcome, including
    a tools server that did not start) and returns the stored run id. A recording failure
    never changes the case outcome: the same CaseResult is returned and a generic line naming
    only the exception class goes to stderr. A caller that must know about failures wraps its
    recorder (the eval runner counts them and exits non-zero)."""
    tracer = safe_tracer(tracer)
    routine = eval_run_id is None
    if routine and not isinstance(tracer, NoopTracer):
        rate = sample_routine if sample_routine is not None else load_tracing().sample_routine
        if not sampled(case_id, run_id or uuid.uuid4().hex, rate):
            tracer = NOOP_TRACER
    result, case = await _run_case(
        case_id,
        spec,
        profile_name,
        database_url=database_url,
        limits=limits,
        adapter=adapter,
        lookup_factory=lookup_factory,
        keep_tool_content=keep_tool_content,
        review=review,
        reviewer_profile_name=reviewer_profile_name,
        reviewer_adapter=reviewer_adapter,
        reviewer_limits=reviewer_limits,
        lessons_provider=lessons_provider,
        tracer=tracer,
        eval_run_id=eval_run_id,
        dataset=dataset,
    )
    result = _end_trace(result, case)
    if routine and not isinstance(tracer, NoopTracer):
        await asyncio.to_thread(tracer.flush)
    if recorder is not None:
        try:
            recorder(result)
        except Exception as exc:
            print(f"error: recording case {case_id} failed ({type(exc).__name__})", file=sys.stderr)
    return result


def _end_trace(result: CaseResult, case: RunTracer) -> CaseResult:
    """End the case trace with the final outcome and put its id on the result's trace (also
    for cases that never reached the loop)."""
    case.end(
        RunEnd(
            status=result.status,
            reason=result.reason,
            totals=result.totals,
            cost_usd=result.cost_usd,
            tool_steps=result.tool_steps,
            wall_ms=result.wall_ms,
            lesson_ids=tuple(result.trace.lesson_ids),
            error_class=result.trace.error_class,
        )
    )
    trace_id = case.trace_id
    if trace_id is None or result.trace.trace_id == trace_id:
        return result
    trace = result.trace.model_copy(update={"trace_id": trace_id})
    return result.model_copy(update={"trace": trace})


async def _run_case(
    case_id: str,
    spec: ModuleSpec,
    profile_name: str,
    *,
    database_url: str,
    limits: HarnessLimits | None,
    adapter: ModelAdapter | None,
    lookup_factory: LookupFactory | None,
    keep_tool_content: bool,
    review: bool,
    reviewer_profile_name: str | None,
    reviewer_adapter: ModelAdapter | None,
    reviewer_limits: HarnessLimits | None,
    lessons_provider: LessonsProvider | None,
    tracer: Tracer,
    eval_run_id: str | None,
    dataset: str | None,
) -> tuple[CaseResult, RunTracer]:
    limits = limits if limits is not None else load_limits()
    profile = get_profile(profile_name)
    adapter = adapter if adapter is not None else make_adapter(profile)
    prompt = load_prompt(spec.prompt)
    required = review and spec.reviewer is not None
    reviewer_profile: ModelProfile = profile
    if required:
        reviewer_limits = reviewer_limits if reviewer_limits is not None else load_reviewer_limits()
        if reviewer_profile_name is not None:
            reviewer_profile = get_profile(reviewer_profile_name)
        if reviewer_adapter is None:
            same = reviewer_profile.name == profile.name
            reviewer_adapter = adapter if same else make_adapter(reviewer_profile)
    case = tracer.start_case(
        TraceMeta(
            case_id=case_id,
            module=spec.name,
            profile=profile.name,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            dataset=dataset,
            eval_run_id=eval_run_id,
        )
    )
    lessons: list[Lesson] = []
    if lessons_provider is not None:
        try:
            lessons = list(await asyncio.to_thread(lessons_provider, case_id))
        except Exception as exc:
            failed = human_review_without_run(
                case_id, spec.name, "internal_error", error_class=type(exc).__name__
            )
            return apply_review(failed, None, required), case
    async with AsyncExitStack() as stack:
        try:
            session = await stack.enter_async_context(
                ToolSession(
                    spec.server_params(database_url),
                    read_timeout_seconds=spec.tool_timeout_seconds,
                )
            )
        except Exception as exc:
            failed = human_review_without_run(
                case_id, spec.name, "tool_failure", error_class=type(exc).__name__
            )
            return apply_review(failed, None, required), case
        if lookup_factory is None:
            lookup_factory = spec.make_lookup_factory(database_url)
            if isinstance(lookup_factory, SupportsClose):
                stack.callback(lookup_factory.close)
        maker = await run_agent(
            case_id=case_id,
            spec=spec,
            adapter=adapter,
            profile=profile,
            tools=session,
            limits=limits,
            prompt=prompt,
            verify_context_factory=lookup_factory,
            keep_tool_content=keep_tool_content,
            lessons=lessons,
            tracer=case,
        )
        outcome: ReviewOutcome | None = None
        if required and maker.status == "proposed":
            assert reviewer_adapter is not None and reviewer_limits is not None
            outcome = await run_review(
                maker=maker,
                spec=spec,
                adapter=reviewer_adapter,
                profile=reviewer_profile,
                tools=session,
                limits=reviewer_limits,
                verify_context_factory=lookup_factory,
                keep_tool_content=keep_tool_content,
                tracer=case,
            )
        return apply_review(maker, outcome, required), case
