"""CaseRunner: set up one case run (limits, model, prompt, tools server) and run the loop.

The runner itself writes nothing and imports no database code. Recording is opt in: a caller
(the eval runner) may pass a recorder callable that writes the finished CaseResult through
the separate write connection in harness/records.py, which the agent never sees.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Protocol, runtime_checkable

from harness.config import HarnessLimits, load_limits
from harness.loop import LookupFactory, human_review_without_run, run_agent
from harness.mcp_client import ToolSession
from harness.models.config import get_profile
from harness.models.factory import make_adapter
from harness.models.types import ModelAdapter
from harness.module_spec import ModuleSpec
from harness.prompts import load_prompt
from harness.trace import CaseResult


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
) -> CaseResult:
    """Run one case end to end. A tools server that fails to start is human_review
    tool_failure with an empty trace. Configuration errors (unknown profile, missing prompt
    file) raise, because they are setup mistakes, not case outcomes.

    database_url is passed explicitly to the tools server and to the module's lookup factory
    (DATABASE_URL in normal runs, TEST_DATABASE_URL in tests). Without a lookup_factory, the
    module builds one from database_url and it is closed when the case ends; a factory passed
    in belongs to the caller, who closes it.

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
) -> CaseResult:
    limits = limits if limits is not None else load_limits()
    profile = get_profile(profile_name)
    adapter = adapter if adapter is not None else make_adapter(profile)
    prompt = load_prompt(spec.prompt)
    async with AsyncExitStack() as stack:
        try:
            session = await stack.enter_async_context(
                ToolSession(
                    spec.server_params(database_url),
                    read_timeout_seconds=spec.tool_timeout_seconds,
                )
            )
        except Exception as exc:
            return human_review_without_run(
                case_id, spec.name, "tool_failure", error_class=type(exc).__name__
            )
        if lookup_factory is None:
            lookup_factory = spec.make_lookup_factory(database_url)
            if isinstance(lookup_factory, SupportsClose):
                stack.callback(lookup_factory.close)
        return await run_agent(
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
