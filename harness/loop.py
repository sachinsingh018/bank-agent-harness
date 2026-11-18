"""The agent loop: send the conversation to the model, run the tools it asks for, repeat.

It stops when the model hands in a valid answer through the module's submit tool or when
anything goes wrong. Every stop other than a valid, verified answer is human_review: nothing is
ever auto-closed. The loop talks only to the model adapter and the tool session; it never
writes anywhere. Rules and the stop reason table: docs/plans/agent-loop.md section 5.

Step rule chosen for a turn that asks for more investigation calls than steps remain: none of
that turn's calls run and the case ends as limit_steps.

A turn with tool calls is processed whatever its stop reason (end_turn or tool_use). A turn
without tool calls under end_turn or tool_use counts as "no answer". A turn with two tool
calls sharing one id is human_review model_error and none of its calls run.

Limits are checked before each model call, right after each model call (token budget) and
after a turn's investigation calls (wall clock), even when that turn holds a valid submission.
One model call or one turn of tool calls can overshoot a limit by at most one call before the
check catches it.

Verifiers may read the database, so they run in a worker thread (asyncio.to_thread) under
asyncio.wait_for with the remaining wall clock time, never less than MIN_VERIFY_SECONDS. A
timeout ends the case as human_review limit_time; the clock is checked again when the verifiers
return, so a slow but finished verification past the limit is also limit_time. Python cannot
kill a thread: a timed out verifier keeps running in the background until it returns, but the
case result does not wait for it.

A verifier that raises (for example a database error) is an infrastructure failure, not a real
check failure: the retry is not spent and the case ends as human_review verifier_error, with
the exception class in the trace.

Tracing (harness/tracing.py): every model call, tool call and verifier run is also sent to the
run's tracer, wrapped so a tracing failure can never change the outcome. The default tracer
does nothing; the caller (runner or checker) starts and ends the trace.

A module with zero verifiers never gets a proposal: a valid submission then ends as
human_review internal_error, because a proposal must always be verified output and an empty
verifier list is a wiring mistake, not evidence that the answer is correct.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from harness.arg_repair import unpack_json_strings
from harness.config import HarnessLimits, effective_max_output_tokens
from harness.lessons import Lesson, render_lessons
from harness.mcp_client import OutcomeCode, ToolErrorPayload, ToolOutcome
from harness.models.config import ModelProfile, cost_usd
from harness.models.types import (
    Message,
    ModelAdapter,
    ModelRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
)
from harness.module_spec import ModuleSpec
from harness.prompts import LoadedPrompt, PromptRef, load_prompt
from harness.trace import (
    CaseResult,
    HumanReviewReason,
    ModelCallRecord,
    RetryReason,
    RunTrace,
    ToolCallRecord,
    ToolResultContent,
)
from harness.tracing import NOOP_RUN, RunTracer, safe_run
from harness.verifiers.base import (
    VerifierResult,
    VerifierRun,
    VerifyContext,
    run_verifiers_detailed,
)

NO_ANSWER_REMINDER = PromptRef(Path("harness/prompts/no_answer_reminder_v1.md"), "v1")
REPEAT_NOTE = "(repeat call: same result as before) "
SUBMIT_ERROR_CODE = "invalid_submission"
MAX_SUBMIT_ERRORS = 20
MAX_ERROR_CHARS = 300
PARSE_ERROR_MESSAGE = "the tool call arguments were not a valid JSON object"
ONE_SUBMIT_MESSAGE = "call the submit tool exactly once per turn"
MIN_VERIFY_SECONDS = 5.0  # verifiers always get at least this long, even near the time limit


class ToolSessionLike(Protocol):
    """What the loop needs from a tool session (harness.mcp_client.ToolSession fits)."""

    @property
    def tools(self) -> list[ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


LookupFactory = Callable[[], Any]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_txn_ids(data: Any, found: set[int]) -> None:
    """Add every int under a key named txn_id, and every int in a list under txn_ids."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "txn_id" and isinstance(value, int) and not isinstance(value, bool):
                found.add(value)
            elif key == "txn_ids" and isinstance(value, list):
                found.update(v for v in value if isinstance(v, int) and not isinstance(v, bool))
            collect_txn_ids(value, found)
    elif isinstance(data, list):
        for item in data:
            collect_txn_ids(item, found)


def txn_ids_in_content(content: str) -> set[int]:
    found: set[int] = set()
    try:
        data = json.loads(content)
    except ValueError:
        return found
    collect_txn_ids(data, found)
    return found


def validation_messages(exc: ValidationError) -> list[str]:
    """Pydantic errors as `loc: msg` only. Input values are never included."""
    out: list[str] = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"]) or "(root)"
        out.append(f"{loc}: {err['msg']}"[:MAX_ERROR_CHARS])
    return out[:MAX_SUBMIT_ERRORS]


def _error_content(code: str, errors: list[str]) -> str:
    return json.dumps({"code": code, "errors": errors}, ensure_ascii=False)


def empty_trace(case_id: str, module: str, error_class: str | None = None) -> RunTrace:
    now = datetime.now(UTC)
    return RunTrace(
        case_id=case_id, module=module, started_at=now, finished_at=now, error_class=error_class
    )


def human_review_without_run(
    case_id: str, module: str, reason: HumanReviewReason, error_class: str | None = None
) -> CaseResult:
    """A human_review result for a case that never reached the loop (for example the tools
    server did not start)."""
    return CaseResult(
        case_id=case_id,
        status="human_review",
        reason=reason,
        findings=None,
        verifier_results=[],
        totals=Usage(input_tokens=0, output_tokens=0),
        cost_usd="0",
        tool_steps=0,
        wall_ms=0,
        trace=empty_trace(case_id, module, error_class),
    )


class _Finish(Exception):
    """Internal: ends the loop with a result."""

    def __init__(
        self,
        reason: HumanReviewReason | None,
        findings: dict[str, Any] | None = None,
        verifier_results: list[VerifierResult] | None = None,
    ) -> None:
        super().__init__(reason or "proposed")
        self.reason = reason
        self.findings = findings
        self.verifier_results = verifier_results or []


@dataclass
class _Run:
    case_id: str
    spec: ModuleSpec
    adapter: ModelAdapter
    profile: ModelProfile
    tools: ToolSessionLike
    limits: HarnessLimits
    prompt: LoadedPrompt
    lookup_factory: LookupFactory
    clock: Callable[[], float]
    keep_tool_content: bool
    lessons: tuple[Lesson, ...] = ()
    tracer: RunTracer = NOOP_RUN
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    start: float = 0.0
    messages: list[Message] = field(default_factory=list)
    totals: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    cost: Decimal = Decimal(0)
    steps: int = 0
    retry_reason: RetryReason | None = None
    seen: set[int] = field(default_factory=set)
    # (tool name, sorted JSON arguments) -> (content, ok, error code) of the first call
    cache: dict[tuple[str, str], tuple[str, bool, OutcomeCode | None]] = field(default_factory=dict)
    model_calls: list[ModelCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_results: list[ToolResultContent] = field(default_factory=list)
    error_class: str | None = None
    submission_repairs: list[str] = field(default_factory=list)
    lesson_ids: list[int] = field(default_factory=list)

    # ---- the loop -------------------------------------------------------------------------

    async def run(self) -> CaseResult:
        self.start = self.clock()
        try:
            await self._loop()
        except _Finish as done:
            return self._result(done)
        except Exception as exc:  # anything unexpected still ends in human review
            self.error_class = type(exc).__name__
            return self._result(_Finish("internal_error"))
        raise AssertionError("the loop only ends through _Finish")  # pragma: no cover

    async def _loop(self) -> None:
        submit_name = self.spec.submit_tool.name
        tool_specs = [*self.tools.tools, self.spec.submit_tool]
        max_out = effective_max_output_tokens(self.limits, self.profile)
        first = self._first_message()
        self.messages.append(Message(role="user", blocks=[TextBlock(text=first)]))
        while True:
            self._check_time()
            self._check_tokens()
            request = ModelRequest(
                system=self.prompt.text,
                messages=list(self.messages),
                tools=tool_specs,
                max_output_tokens=max_out,
            )
            try:
                response = await self.adapter.complete(request)
            except Exception as exc:
                self.error_class = type(exc).__name__  # never the message
                raise _Finish("model_error") from None
            call_cost = cost_usd(self.profile, response.usage)
            self.totals = self.totals + response.usage
            self.cost += call_cost
            record = ModelCallRecord(
                index=len(self.model_calls),
                provider=response.provider,
                model=response.model,
                prompt_version=self.prompt.version,
                prompt_sha256=self.prompt.sha256,
                usage=response.usage,
                cost_usd=str(call_cost),
                latency_ms=response.latency_ms,
                stop_reason=response.stop_reason,
                request_id=response.request_id,
            )
            self.model_calls.append(record)
            self.tracer.model_call(record, request, response.message)
            self.messages.append(response.message)
            self._check_tokens()
            stop = response.stop_reason
            if stop == "max_tokens":
                raise _Finish("limit_output")
            if stop == "refusal":
                raise _Finish("refusal")
            if stop == "other":
                raise _Finish("model_error")
            calls = response.message.tool_calls()
            if len({c.id for c in calls}) != len(calls):
                raise _Finish("model_error")  # duplicate call ids: none of the turn runs
            if not calls:
                self._no_answer()
                continue
            await self._handle_calls(calls, submit_name)

    def _first_message(self) -> str:
        """The module's case message, plus the lessons block when there are lessons. Lessons
        without a template are a wiring mistake (internal_error)."""
        first = self.spec.build_case_message(self.case_id)
        if not self.lessons:
            return first
        if self.spec.lessons_template is None:
            raise ValueError("lessons were given but the module has no lessons template")
        block = render_lessons(load_prompt(self.spec.lessons_template).text, self.lessons)
        self.lesson_ids = [lesson.id for lesson in self.lessons]
        return f"{first}\n\n{block}"

    def _check_time(self) -> None:
        if self.clock() - self.start >= self.limits.max_wall_seconds:
            raise _Finish("limit_time")

    def _check_tokens(self) -> None:
        if self.totals.total_tokens >= self.limits.max_total_tokens:
            raise _Finish("limit_tokens")

    def _use_retry(self, reason: RetryReason) -> bool:
        """Spend the single retry. False when it was already spent."""
        if self.retry_reason is not None:
            return False
        self.retry_reason = reason
        return True

    def _no_answer(self) -> None:
        if not self._use_retry("no_answer"):
            raise _Finish("no_answer")
        text = load_prompt(NO_ANSWER_REMINDER).text
        text = text.replace("{submit_tool}", self.spec.submit_tool.name).strip()
        self.messages.append(Message(role="user", blocks=[TextBlock(text=text)]))

    # ---- tool calls -----------------------------------------------------------------------

    async def _handle_calls(self, calls: list[ToolCallBlock], submit_name: str) -> None:
        investigation = [c for c in calls if c.name != submit_name]
        submits = [c for c in calls if c.name == submit_name]
        if self.steps + len(investigation) > self.limits.max_tool_steps:
            raise _Finish("limit_steps")  # none of this turn's calls run
        # Results keyed by position in the turn, so they go back in call order.
        results: dict[int, ToolResultBlock] = {}
        for pos, call in enumerate(calls):
            if call.name != submit_name:
                results[pos] = await self._investigate(call)
        self._check_time()
        if submits:
            submit_blocks = iter(await self._submit(submits))
            for pos, call in enumerate(calls):
                if call.name == submit_name:
                    results[pos] = next(submit_blocks)
        blocks = [results[pos] for pos in range(len(calls))]
        self.messages.append(Message(role="user", blocks=list(blocks)))

    def _record_tool(
        self,
        call: ToolCallBlock,
        *,
        step: int | None,
        content: str,
        ok: bool,
        error_code: OutcomeCode | None,
        duration_ms: int = 0,
        truncated: bool = False,
        repeat: bool = False,
        arguments: dict[str, Any] | None = None,
        arg_repairs: list[str] | None = None,
    ) -> None:
        index = len(self.tool_calls)
        record = ToolCallRecord(
            index=index,
            step=step,
            name=call.name,
            arguments=call.arguments if arguments is None else arguments,
            ok=ok,
            error_code=error_code,
            duration_ms=duration_ms,
            size=len(content),
            truncated=truncated,
            repeat=repeat,
            content_sha256=_sha256(content),
            arg_repairs=list(arg_repairs or []),
        )
        self.tool_calls.append(record)
        self.tracer.tool_call(record, content)
        if self.keep_tool_content:
            self.tool_results.append(ToolResultContent(index=index, content=content))

    async def _investigate(self, call: ToolCallBlock) -> ToolResultBlock:
        self.steps += 1
        step = self.steps
        if call.parse_error is not None:
            payload = ToolErrorPayload(code="invalid_argument", message=PARSE_ERROR_MESSAGE)
            content = payload.model_dump_json()
            self._record_tool(
                call, step=step, content=content, ok=False, error_code="invalid_argument"
            )
            return ToolResultBlock(tool_call_id=call.id, content=content, is_error=True)
        arguments, repairs = self._repair(call)
        key = (call.name, json.dumps(arguments, sort_keys=True))
        cached = self.cache.get(key)
        if cached is not None:
            first_content, ok, code = cached
            content = REPEAT_NOTE + first_content
            self._record_tool(
                call,
                step=step,
                content=content,
                ok=ok,
                error_code=code,
                repeat=True,
                arguments=arguments,
                arg_repairs=repairs,
            )
            return ToolResultBlock(tool_call_id=call.id, content=content, is_error=not ok)
        outcome = await self.tools.call(call.name, arguments)
        self._record_tool(
            call,
            step=step,
            content=outcome.content,
            ok=outcome.ok,
            error_code=outcome.error_code,
            duration_ms=outcome.duration_ms,
            truncated=outcome.truncated,
            arguments=arguments,
            arg_repairs=repairs,
        )
        if outcome.session_dead:
            raise _Finish("tool_failure")
        self.cache[key] = (outcome.content, outcome.ok, outcome.error_code)
        if outcome.ok:
            self.seen |= txn_ids_in_content(outcome.content)
        return ToolResultBlock(
            tool_call_id=call.id, content=outcome.content, is_error=not outcome.ok
        )

    def _repair(self, call: ToolCallBlock) -> tuple[dict[str, Any], list[str]]:
        """Unpack JSON-encoded list or object strings the tool's schema expects. Unknown tools
        are left alone (the session reports them)."""
        schema = next((t.input_schema for t in self.tools.tools if t.name == call.name), None)
        if schema is None:
            return call.arguments, []
        repaired, repairs = unpack_json_strings(call.arguments, schema)
        assert isinstance(repaired, dict)
        return repaired, repairs

    # ---- submission -----------------------------------------------------------------------

    async def _submit(self, submits: list[ToolCallBlock]) -> list[ToolResultBlock]:
        """Validate and verify the submission. Finishes the loop on success or on a second
        failure; otherwise returns error results for the submit calls and spends the retry."""
        verifier_results: list[VerifierResult] = []
        findings: dict[str, Any] | None = None
        kind: RetryReason = "schema_failed"
        errors: list[str]
        if len(submits) > 1:
            errors = [ONE_SUBMIT_MESSAGE]
        elif submits[0].parse_error is not None:
            errors = [PARSE_ERROR_MESSAGE]
        else:
            # Unpacking JSON-encoded strings is not a retry; the paths go on the trace.
            arguments, repairs = unpack_json_strings(
                submits[0].arguments, self.spec.submit_tool.input_schema
            )
            self.submission_repairs = repairs
            try:
                output = self.spec.output_model.model_validate(arguments)
            except ValidationError as exc:
                errors = validation_messages(exc)
            else:
                findings = output.model_dump(mode="json")
                if not self.spec.verifiers:
                    # Never propose unverified output (see the module docstring).
                    self._record_tool(submits[0], step=None, content="", ok=True, error_code=None)
                    raise _Finish("internal_error", findings, [])
                verified = await self._verify(output, submits[0])
                verifier_results = verified.results
                self.tracer.verifiers(verifier_results)
                if self.clock() - self.start >= self.limits.max_wall_seconds:
                    self._record_tool(
                        submits[0], step=None, content="", ok=False, error_code="timeout"
                    )
                    raise _Finish("limit_time", findings, verifier_results)
                if verified.error_class is not None:
                    # Infrastructure failure: do not spend the retry on it.
                    self.error_class = verified.error_class
                    self._record_tool(
                        submits[0], step=None, content="", ok=False, error_code="internal"
                    )
                    raise _Finish("verifier_error", findings, verifier_results)
                errors = [
                    f"{r.name}: {e}"[:MAX_ERROR_CHARS]
                    for r in verifier_results
                    if not r.passed
                    for e in (r.errors or ["failed"])
                ][:MAX_SUBMIT_ERRORS]
                kind = "verifier_failed"
                if all(r.passed for r in verifier_results):
                    self._record_tool(submits[0], step=None, content="", ok=True, error_code=None)
                    raise _Finish(None, findings, verifier_results)
        content = _error_content(SUBMIT_ERROR_CODE, errors)
        for call in submits:
            self._record_tool(
                call, step=None, content=content, ok=False, error_code="invalid_argument"
            )
        if not self._use_retry(kind):
            raise _Finish(kind, findings, verifier_results)
        return [ToolResultBlock(tool_call_id=c.id, content=content, is_error=True) for c in submits]

    async def _verify(self, output: BaseModel, call: ToolCallBlock) -> VerifierRun:
        """Run the verifiers in a worker thread, bounded by the remaining wall clock time."""
        seen = frozenset(self.seen)
        remaining = self.limits.max_wall_seconds - (self.clock() - self.start)
        timeout = max(MIN_VERIFY_SECONDS, remaining)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._verify_sync, output, seen), timeout
            )
        except TimeoutError:
            self._record_tool(call, step=None, content="", ok=False, error_code="timeout")
            raise _Finish("limit_time") from None

    def _verify_sync(self, output: BaseModel, seen: frozenset[int]) -> VerifierRun:
        """Runs in the worker thread. A failing lookup factory counts as a verifier error."""
        try:
            lookup = self.lookup_factory()
        except Exception as exc:
            name = type(exc).__name__
            failed = VerifierResult(
                name="lookup", passed=False, errors=[f"verifier error ({name})"]
            )
            return VerifierRun(results=[failed], error_class=name)
        ctx = VerifyContext(case_id=self.case_id, seen_txn_ids=seen, lookup=lookup)
        return run_verifiers_detailed(self.spec.verifiers, output, ctx)

    # ---- result ---------------------------------------------------------------------------

    def _trace_messages(self) -> list[Message]:
        if self.keep_tool_content:
            return list(self.messages)
        out: list[Message] = []
        for message in self.messages:
            blocks = [
                b.model_copy(update={"content": "sha256:" + _sha256(b.content)})
                if isinstance(b, ToolResultBlock)
                else b
                for b in message.blocks
            ]
            out.append(message.model_copy(update={"blocks": blocks}))
        return out

    def _result(self, done: _Finish) -> CaseResult:
        trace = RunTrace(
            case_id=self.case_id,
            module=self.spec.name,
            started_at=self.started_at,
            finished_at=datetime.now(UTC),
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            tool_results=self.tool_results,
            messages=self._trace_messages(),
            retry_used=self.retry_reason is not None,
            retry_reason=self.retry_reason,
            seen_txn_ids=sorted(self.seen),
            error_class=self.error_class,
            submission_repairs=self.submission_repairs,
            lesson_ids=self.lesson_ids,
            trace_id=self.tracer.trace_id,
        )
        wall_ms = max(0, int((self.clock() - self.start) * 1000))
        return CaseResult(
            case_id=self.case_id,
            status="proposed" if done.reason is None else "human_review",
            reason=done.reason,
            findings=done.findings,
            verifier_results=done.verifier_results,
            totals=self.totals,
            cost_usd=str(self.cost),
            tool_steps=self.steps,
            wall_ms=wall_ms,
            trace=trace,
        )


async def run_agent(
    *,
    case_id: str,
    spec: ModuleSpec,
    adapter: ModelAdapter,
    profile: ModelProfile,
    tools: ToolSessionLike,
    limits: HarnessLimits,
    prompt: LoadedPrompt,
    verify_context_factory: LookupFactory,
    clock: Callable[[], float] = time.monotonic,
    keep_tool_content: bool = True,
    lessons: Sequence[Lesson] = (),
    tracer: RunTracer = NOOP_RUN,
) -> CaseResult:
    """Run one case. Returns proposed only for a valid submission that passes every verifier;
    every other ending is human_review with the partial trace.

    verify_context_factory returns the module's read-only lookup for VerifyContext. lessons,
    when not empty, are rendered through spec.lessons_template and added to the first user
    message; their ids go on the trace. tracer receives every model call, tool call and
    verifier run (it is not ended here); its trace id goes on the trace."""
    run = _Run(
        case_id=case_id,
        spec=spec,
        adapter=adapter,
        profile=profile,
        tools=tools,
        limits=limits,
        prompt=prompt,
        lookup_factory=verify_context_factory,
        clock=clock,
        keep_tool_content=keep_tool_content,
        lessons=tuple(lessons),
        tracer=safe_run(tracer),
    )
    return await run.run()
