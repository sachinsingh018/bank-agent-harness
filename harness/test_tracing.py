"""Tracing: the calls the harness makes to its tracer, safety, sampling and the Langfuse wiring.

Nothing is sent anywhere: the Langfuse client is always a fake here, and the repo conftest
removes the LANGFUSE_* env vars for every test."""

import asyncio
import dataclasses
import inspect
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pytest
from mcp import StdioServerParameters
from pydantic import BaseModel, ConfigDict

import harness.tracing as tracing
from harness.config import HarnessLimits, load_tracing
from harness.loop import run_agent
from harness.models.scripted_adapter import ScriptedAdapter, ScriptItem, tool_call_response
from harness.models.types import Message, ModelRequest, ToolSpec, Usage
from harness.module_spec import ModuleSpec, ReviewerSpec
from harness.prompts import PromptRef
from harness.review import run_review
from harness.runner import run_case
from harness.test_loop import PROMPT, AlwaysPass, FakeTools, _call, _err, _spec, _submit
from harness.trace import CaseResult, ModelCallRecord, RunTrace, ToolCallRecord
from harness.tracing import (
    NOOP_RUN,
    NOOP_TRACER,
    LangfuseTracer,
    NoopTracer,
    RunEnd,
    RunTracer,
    TraceMeta,
    sampled,
    tracer_from_env,
)
from harness.verifiers.base import VerifierResult

Event = tuple[Any, ...]


class RecordingRun:
    def __init__(self, events: list[Event], who: str) -> None:
        self.events = events
        self.who = who

    @property
    def trace_id(self) -> str | None:
        return "trace-1"

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        self.events.append(("model", self.who, record.index))

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        self.events.append(("tool", self.who, record.name, record.ok, record.error_code))

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        self.events.append(("verifiers", self.who, tuple(r.passed for r in results)))

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        self.events.append(("review", self.who))
        return RecordingRun(self.events, "checker")

    def end(self, end: RunEnd) -> None:
        self.events.append(("end", self.who, end.status, end.reason))


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.metas: list[TraceMeta] = []

    @property
    def host(self) -> str | None:
        return "https://langfuse.example"

    def start_case(self, meta: TraceMeta) -> RunTracer:
        self.metas.append(meta)
        self.events.append(("start", meta.case_id))
        return RecordingRun(self.events, "maker")

    def flush(self) -> None:
        self.events.append(("flush",))


class Broken(RuntimeError):
    pass


class RaisingRun:
    @property
    def trace_id(self) -> str | None:
        raise Broken

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        raise Broken

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        raise Broken

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        raise Broken

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        raise Broken

    def end(self, end: RunEnd) -> None:
        raise Broken


class RaisingTracer:
    @property
    def host(self) -> str | None:
        raise Broken

    def start_case(self, meta: TraceMeta) -> RunTracer:
        raise Broken

    def flush(self) -> None:
        raise Broken


def _agent(
    script: Sequence[ScriptItem], tracer: RunTracer = NOOP_RUN, tools: FakeTools | None = None
) -> CaseResult:
    adapter = ScriptedAdapter(script)
    return asyncio.run(
        run_agent(
            case_id="C-1",
            spec=_spec(),
            adapter=adapter,
            profile=adapter.profile,
            tools=tools or FakeTools(),
            limits=HarnessLimits(),
            prompt=PROMPT,
            verify_context_factory=lambda: None,
            tracer=tracer,
        )
    )


HAPPY: list[ScriptItem] = [
    tool_call_response([_call("lookup", id=7)]),
    tool_call_response([_submit()]),
]


# ---- calls and their order ----------------------------------------------------------------


def test_happy_path_calls_in_order() -> None:
    events: list[Event] = []
    result = _agent(HAPPY, RecordingRun(events, "maker"))
    assert result.status == "proposed"
    assert events == [
        ("model", "maker", 0),
        ("tool", "maker", "lookup", True, None),
        ("model", "maker", 1),
        ("verifiers", "maker", (True,)),
        ("tool", "maker", "submit_answer", True, None),
    ]
    assert result.trace.trace_id == "trace-1"


def test_tool_error_is_traced_with_its_code() -> None:
    events: list[Event] = []
    tools = FakeTools({"lookup:8": _err("not_found")})
    script: list[ScriptItem] = [
        tool_call_response([_call("lookup", id=8)], id_prefix="a"),
        tool_call_response([_call("lookup", id=7)], id_prefix="b"),
        tool_call_response([_submit()], id_prefix="c"),
    ]
    result = _agent(script, RecordingRun(events, "maker"), tools)
    assert result.status == "proposed"
    assert ("tool", "maker", "lookup", False, "not_found") in events
    assert events.index(("tool", "maker", "lookup", False, "not_found")) == 1


def test_verifier_retry_is_traced() -> None:
    events: list[Event] = []
    script: list[ScriptItem] = [
        tool_call_response([_submit(ids=[7])], id_prefix="a"),
        tool_call_response([_call("lookup", id=7)], id_prefix="b"),
        tool_call_response([_submit(ids=[7])], id_prefix="c"),
    ]
    result = _agent(script, RecordingRun(events, "maker"))
    assert result.status == "proposed" and result.trace.retry_reason == "verifier_failed"
    assert events == [
        ("model", "maker", 0),
        ("verifiers", "maker", (False,)),
        ("tool", "maker", "submit_answer", False, "invalid_argument"),
        ("model", "maker", 1),
        ("tool", "maker", "lookup", True, None),
        ("model", "maker", 2),
        ("verifiers", "maker", (True,)),
        ("tool", "maker", "submit_answer", True, None),
    ]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approve", "reject"]


def _review_spec(tmp_path: Path) -> ModuleSpec:
    prompt = tmp_path / "review_v1.md"
    prompt.write_text("review prompt", encoding="utf-8")
    reviewer = ReviewerSpec(
        prompt=PromptRef(prompt, "v1"),
        output_model=Verdict,
        submit_tool=ToolSpec(name="submit_review", description="d", input_schema={}),
        build_review_message=lambda case_id, findings: f"Review {case_id}.",
        make_verifiers=lambda findings: [AlwaysPass()],
    )
    return dataclasses.replace(_spec(), reviewer=reviewer)


def _maker() -> CaseResult:
    now = RunTrace(case_id="C-1", module="test", started_at=_now(), finished_at=_now())
    return CaseResult(
        case_id="C-1",
        status="proposed",
        reason=None,
        findings={"verdict": "yes", "txn_ids": [7]},
        verifier_results=[VerifierResult(name="seen", passed=True, errors=[])],
        totals=Usage(input_tokens=1, output_tokens=1),
        cost_usd="0",
        tool_steps=1,
        wall_ms=1,
        trace=now,
    )


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime(2026, 1, 2, tzinfo=UTC)


def _run_review(spec: ModuleSpec, tracer: RunTracer) -> Any:
    adapter = ScriptedAdapter([tool_call_response([("submit_review", {"verdict": "approve"})])])
    return asyncio.run(
        run_review(
            maker=_maker(),
            spec=spec,
            adapter=adapter,
            profile=adapter.profile,
            tools=FakeTools(),
            limits=HarnessLimits(),
            verify_context_factory=lambda: None,
            tracer=tracer,
        )
    )


def test_review_is_its_own_child_span_with_the_checker_calls(tmp_path: Path) -> None:
    events: list[Event] = []
    outcome = _run_review(_review_spec(tmp_path), RecordingRun(events, "maker"))
    assert outcome.verdict == "approve"
    assert events == [
        ("review", "maker"),
        ("model", "checker", 0),
        ("verifiers", "checker", (True,)),
        ("tool", "checker", "submit_review", True, None),
        ("end", "checker", "approve", None),
    ]
    assert outcome.trace.trace_id == "trace-1"


def _missing_server_spec(tmp_path: Path) -> ModuleSpec:
    prompt = tmp_path / "system_v1.md"
    prompt.write_text("system", encoding="utf-8")

    def params(url: str) -> StdioServerParameters:
        return StdioServerParameters(command="/nonexistent/tools-server", args=[])

    return dataclasses.replace(_spec(), server_params=params, prompt=PromptRef(prompt, "v1"))


def _case(tmp_path: Path, tracer: Any, **kwargs: Any) -> CaseResult:
    return asyncio.run(
        run_case(
            "C-1",
            _missing_server_spec(tmp_path),
            "scripted",
            database_url="unused",
            adapter=ScriptedAdapter([]),
            tracer=tracer,
            **kwargs,
        )
    )


def test_run_case_starts_and_ends_the_case_trace(tmp_path: Path) -> None:
    tracer = RecordingTracer()
    result = _case(tmp_path, tracer, eval_run_id="run-1", dataset="smoke@v2")
    assert result.reason == "tool_failure"
    assert tracer.events == [("start", "C-1"), ("end", "maker", "human_review", "tool_failure")]
    meta = tracer.metas[0]
    assert (meta.module, meta.profile, meta.prompt_version) == ("test", "scripted", "v1")
    assert (meta.eval_run_id, meta.dataset) == ("run-1", "smoke@v2")
    assert len(meta.prompt_sha256) == 64
    assert result.trace.trace_id == "trace-1"


def test_routine_run_is_sampled_and_flushed(tmp_path: Path) -> None:
    skipped = RecordingTracer()
    _case(tmp_path, skipped, run_id="r", sample_routine=0.0)
    assert skipped.events == []
    traced = RecordingTracer()
    _case(tmp_path, traced, run_id="r", sample_routine=1.0)
    assert traced.events[0] == ("start", "C-1") and traced.events[-1] == ("flush",)


# ---- safety ---------------------------------------------------------------------------------


def _outcome(result: CaseResult) -> Any:
    return (
        result.status,
        result.reason,
        result.findings,
        [c.model_dump() for c in result.trace.tool_calls],
        [c.model_dump() for c in result.trace.model_calls],
    )


def test_raising_tracer_does_not_change_the_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = _agent(HAPPY)
    broken = _agent(HAPPY, RaisingRun())
    assert _outcome(broken) == _outcome(plain)
    assert broken.trace.trace_id is None
    review = _run_review(_review_spec(tmp_path), RaisingRun())
    assert review.verdict == "approve"
    case = _case(tmp_path, RaisingTracer(), eval_run_id="run-1")
    assert case.reason == "tool_failure"
    err = capsys.readouterr().err
    assert "tracing failed (Broken)" in err


def test_noop_tracer_is_the_default() -> None:
    assert inspect.signature(run_case).parameters["tracer"].default is NOOP_TRACER
    assert inspect.signature(run_agent).parameters["tracer"].default is NOOP_RUN
    assert inspect.signature(run_review).parameters["tracer"].default is NOOP_RUN
    assert _agent(HAPPY).trace.trace_id is None


def test_tracing_config() -> None:
    config = load_tracing()
    assert config.enabled is True and config.sample_routine == 0.1


# ---- sampling -------------------------------------------------------------------------------


def test_sampling_is_deterministic() -> None:
    picks = [sampled(f"A-{i}", "run-1", 0.1) for i in range(2000)]
    assert picks == [sampled(f"A-{i}", "run-1", 0.1) for i in range(2000)]
    assert 0.07 < sum(picks) / len(picks) < 0.13
    assert not any(sampled(f"A-{i}", "run-1", 0.0) for i in range(100))
    assert all(sampled(f"A-{i}", "run-1", 1.0) for i in range(100))


# ---- Langfuse wiring with a fake client --------------------------------------------------------

SECRETS = {
    "LANGFUSE_PUBLIC_KEY": "pk-lf-test-public-123",
    "LANGFUSE_SECRET_KEY": "sk-lf-test-secret-456",
    "LANGFUSE_HOST": "https://langfuse.example.test",
    "ANTHROPIC_API_KEY": "sk-ant-test-key-789",
    "DATABASE_URL": "postgresql+psycopg://user:pw@remote.example:5432/db",
}


class FakeObservation:
    def __init__(self, client: "FakeClient", name: str) -> None:
        self.client = client
        self.trace_id = "f" * 32
        self.id = name

    def start_observation(self, **kwargs: Any) -> "FakeObservation":
        self.client.sent.append(("child", kwargs))
        return FakeObservation(self.client, str(kwargs.get("name")))

    def update(self, **kwargs: Any) -> None:
        self.client.sent.append(("update", kwargs))

    def end(self) -> None:
        self.client.sent.append(("end", {"id": self.id}))


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.flushed = 0

    def start_observation(self, **kwargs: Any) -> FakeObservation:
        self.sent.append(("root", kwargs))
        return FakeObservation(self, str(kwargs.get("name")))

    def flush(self) -> None:
        self.flushed += 1


@pytest.mark.parametrize("missing", tracing.TRACING_ENV)
def test_langfuse_needs_all_three_env_vars(missing: str, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple[str, str, str]] = []

    def factory(public: str, secret: str, host: str) -> FakeClient:
        made.append((public, secret, host))
        return FakeClient()

    monkeypatch.setattr(tracing, "_langfuse_client", factory)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    assert isinstance(tracer_from_env(True), NoopTracer)
    monkeypatch.setenv(missing, SECRETS[missing])
    assert isinstance(tracer_from_env(False), NoopTracer)
    assert made == []
    tracer = tracer_from_env(True)
    assert isinstance(tracer, LangfuseTracer)
    assert tracer.host == SECRETS["LANGFUSE_HOST"]
    assert made == [tuple(SECRETS[n] for n in tracing.TRACING_ENV)]


def test_a_failing_client_gives_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(public: str, secret: str, host: str) -> FakeClient:
        raise Broken

    env = dict(SECRETS)
    assert isinstance(tracer_from_env(True, env=env, client_factory=factory), NoopTracer)


def test_nothing_secret_is_sent_and_fields_are_capped() -> None:
    client = FakeClient()
    tracer = tracer_from_env(True, env=SECRETS, client_factory=lambda p, s, h: client)
    assert isinstance(tracer, LangfuseTracer)
    leaky = (
        f"key {SECRETS['ANTHROPIC_API_KEY']} url {SECRETS['DATABASE_URL']} "
        f"secret {SECRETS['LANGFUSE_SECRET_KEY']} " + "x" * 20_000
    )
    tools = FakeTools()

    async def leaky_call(name: str, arguments: dict[str, Any]) -> Any:
        from harness.mcp_client import ToolOutcome

        content = json.dumps({"txn_id": 7, "note": leaky})
        return ToolOutcome(
            ok=True, content=content, error_code=None, duration_ms=1, size=1, truncated=False
        )

    tools.call = leaky_call  # type: ignore[method-assign]
    meta = TraceMeta(
        case_id="C-1",
        module="aml",
        profile="scripted",
        prompt_version="v1",
        prompt_sha256="0" * 64,
        eval_run_id="run-1",
    )
    case = tracer.start_case(meta)
    result = _agent(HAPPY, case, tools)
    case.end(
        RunEnd(
            status=result.status,
            reason=result.reason,
            totals=result.totals,
            cost_usd=result.cost_usd,
            tool_steps=result.tool_steps,
            wall_ms=result.wall_ms,
        )
    )
    tracer.flush()
    assert result.status == "proposed" and result.trace.trace_id == "f" * 32
    sent = json.dumps(client.sent, default=str)
    for value in SECRETS.values():
        assert value not in sent
    kinds = [(kind, kwargs.get("as_type")) for kind, kwargs in client.sent if kind != "end"]
    assert kinds[0] == ("root", "span")
    assert ("child", "generation") in kinds and ("child", "span") in kinds
    tool_span = next(k for kind, k in client.sent if k.get("name") == "tool-lookup")
    assert len(tool_span["output"]) < tracing.MAX_FIELD_CHARS + 100
    assert tool_span["metadata"]["size"] > 0
    generation = next(k for kind, k in client.sent if k.get("as_type") == "generation")
    assert generation["usage_details"]["input"] == 100
    assert client.flushed == 1
