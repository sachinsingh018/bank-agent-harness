"""CaseRunner: set up one case run (limits, model, prompt, tools server) and run the loop.

The runner itself writes nothing and imports no database code. Recording is opt in: a caller
(the eval runner) may pass a recorder callable that writes the finished CaseResult through
the separate write connection in harness/records.py, which the agent never sees.

Maker-checker: when the module has a reviewer and review is on, a proposed maker result goes to
the checker in the same tool session (harness/review.py). The checker uses the maker's profile
unless reviewer_profile_name is given, and the [reviewer] limits.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Protocol, runtime_checkable

from harness.config import HarnessLimits, load_limits, load_reviewer_limits
from harness.loop import LookupFactory, human_review_without_run, run_agent
from harness.mcp_client import ToolSession
from harness.models.config import ModelProfile, get_profile
from harness.models.factory import make_adapter
from harness.models.types import ModelAdapter
from harness.module_spec import ModuleSpec
from harness.prompts import load_prompt
from harness.review import apply_review, run_review
from harness.trace import CaseResult, ReviewOutcome


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

    recorder, when given, is called once with the finished result (every outcome, including
    a tools server that did not start) and returns the stored run id. A recording failure
    never changes the case outcome: the same CaseResult is returned and a generic line naming
    only the exception class goes to stderr. A caller that must know about failures wraps its
    recorder (the eval runner counts them and exits non-zero)."""
    result = await _run_case(
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
    )
    if recorder is not None:
        try:
            recorder(result)
        except Exception as exc:
            print(f"error: recording case {case_id} failed ({type(exc).__name__})", file=sys.stderr)
    return result


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
) -> CaseResult:
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
            return apply_review(failed, None, required)
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
            )
        return apply_review(maker, outcome, required)
