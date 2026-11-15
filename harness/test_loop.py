"""The agent loop with a scripted model and a fake tool session. No network, no database.

The output model and verifier are defined here, so the harness stays module agnostic."""

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

import pytest
from mcp import StdioServerParameters
from pydantic import BaseModel, ConfigDict, ValidationError

from harness.config import HarnessLimits
from harness.loop import REPEAT_NOTE, collect_txn_ids, run_agent, txn_ids_in_content
from harness.mcp_client import ToolOutcome
from harness.models.scripted_adapter import (
    ScriptedAdapter,
    ScriptItem,
    text_response,
    tool_call_response,
)
from harness.models.types import (
    Message,
    ModelRequest,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
)
from harness.module_spec import ModuleSpec
from harness.prompts import LoadedPrompt, PromptRef
from harness.trace import CaseResult, RunTrace
from harness.verifiers.base import VerifierResult, VerifyContext

SUBMIT = "submit_answer"
SECRET = "sk-secret-value-in-message"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["yes", "no"]
    txn_ids: list[int]


class SeenVerifier:
    """Passes when every cited id appeared in a tool result."""

    name = "seen"

    def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
        assert isinstance(output, Answer)
        missing = [i for i in output.txn_ids if i not in ctx.seen_txn_ids]
        return VerifierResult(
            name=self.name, passed=not missing, errors=[f"txn {i} not seen" for i in missing]
        )


def _spec(verifiers: Sequence[SeenVerifier] = (SeenVerifier(),)) -> ModuleSpec:
    def params(url: str) -> StdioServerParameters:
        raise AssertionError("not used in pure tests")

    return ModuleSpec(
        name="test",
        server_params=params,
        tool_timeout_seconds=1.0,
        prompt=PromptRef(Path("unused_v1.md"), "v1"),
        output_model=Answer,
        submit_tool=ToolSpec(name=SUBMIT, description="submit", input_schema={}),
        verifiers=tuple(verifiers),
        eval_dataset=Path("unused.json"),
        build_case_message=lambda case_id: f"Investigate {case_id}.",
        make_lookup_factory=lambda url: lambda: None,
    )


PROMPT = LoadedPrompt(text="system prompt", version="v1", path="p_v1.md", sha256="abc")


def _ok(data: Any) -> ToolOutcome:
    content = json.dumps(data)
    return ToolOutcome(
        ok=True, content=content, error_code=None, duration_ms=1, size=len(content), truncated=False
    )


def _err(code: Literal["not_found", "internal"], dead: bool = False) -> ToolOutcome:
    content = json.dumps({"code": code, "message": "m"})
    return ToolOutcome(
        ok=False,
        content=content,
        error_code=code,
        duration_ms=1,
        size=len(content),
        truncated=False,
        session_dead=dead,
    )


class FakeTools:
    def __init__(self, outcomes: dict[str, ToolOutcome] | None = None) -> None:
        self.tools = [
            ToolSpec(name="lookup", description="d", input_schema={"type": "object"}),
            ToolSpec(name="other", description="d", input_schema={"type": "object"}),
        ]
        self.outcomes = outcomes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        key = f"{name}:{arguments.get('id')}"
        if key in self.outcomes:
            return self.outcomes[key]
        return _ok({"txn_id": arguments.get("id", 0), "name": name})


def _run(
    script: Sequence[ScriptItem],
    *,
    tools: FakeTools | None = None,
    limits: HarnessLimits | None = None,
    spec: ModuleSpec | None = None,
    clock: Callable[[], float] | None = None,
    keep: bool = True,
) -> tuple[CaseResult, ScriptedAdapter, FakeTools]:
    adapter = ScriptedAdapter(script)
    fake = tools or FakeTools()
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    result = asyncio.run(
        run_agent(
            case_id="C-1",
            spec=spec or _spec(),
            adapter=adapter,
            profile=adapter.profile,
            tools=fake,
            limits=limits or HarnessLimits(),
            prompt=PROMPT,
            verify_context_factory=lambda: None,
            keep_tool_content=keep,
            **kwargs,
        )
    )
    assert result.status in ("proposed", "human_review")
    return result, adapter, fake


def _call(name: str, **args: Any) -> tuple[str, dict[str, Any]]:
    return (name, args)


def _submit(verdict: str = "yes", ids: list[int] | None = None) -> tuple[str, dict[str, Any]]:
    return (SUBMIT, {"verdict": verdict, "txn_ids": [7] if ids is None else ids})


def _resp(stop: StopReason, text: str = "x") -> ModelResponse:
    return text_response(text, stop_reason=stop)


# ---- happy path -------------------------------------------------------------------------


def test_happy_path_is_proposed() -> None:
    result, adapter, fake = _run(
        [tool_call_response([_call("lookup", id=7)]), tool_call_response([_submit()])]
    )
    assert result.status == "proposed" and result.reason is None
    assert result.findings == {"verdict": "yes", "txn_ids": [7]}
    assert [r.passed for r in result.verifier_results] == [True]
    assert result.tool_steps == 1
    assert result.cost_usd == "0"
    assert result.totals.input_tokens == 200
    assert result.trace.seen_txn_ids == [7]
    assert not result.trace.retry_used
    assert fake.calls == [("lookup", {"id": 7})]
    first = adapter.requests[0]
    assert first.system == "system prompt"
    assert first.messages[0].text() == "Investigate C-1."
    assert [t.name for t in first.tools] == ["lookup", "other", SUBMIT]
    assert first.max_output_tokens == 16_000
    assert len(result.trace.model_calls) == 2
    assert result.trace.model_calls[0].prompt_sha256 == "abc"
    submit_record = result.trace.tool_calls[-1]
    assert submit_record.step is None and submit_record.ok


def test_max_output_tokens_is_the_smaller_cap() -> None:
    _, adapter, _ = _run(
        [tool_call_response([_submit()], usage=None)],
        limits=HarnessLimits(max_output_tokens_per_call=500),
        tools=FakeTools(),
    )
    assert adapter.requests[0].max_output_tokens == 500


def test_submit_with_investigation_in_same_turn() -> None:
    result, _, fake = _run([tool_call_response([_call("lookup", id=7), _submit()])])
    assert result.status == "proposed"
    assert fake.calls == [("lookup", {"id": 7})]
    assert result.tool_steps == 1


# ---- stop reasons -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stop", "reason"),
    [("max_tokens", "limit_output"), ("refusal", "refusal"), ("other", "model_error")],
)
def test_stop_reasons(stop: StopReason, reason: str) -> None:
    result, _, _ = _run([_resp(stop)])
    assert result.status == "human_review" and result.reason == reason
    assert result.findings is None


def test_end_turn_then_answer_uses_reminder() -> None:
    result, adapter, _ = _run(
        [
            _resp("end_turn"),
            tool_call_response([_call("lookup", id=7)]),
            tool_call_response([_submit()], id_prefix="s"),
        ]
    )
    assert result.status == "proposed"
    assert result.trace.retry_used and result.trace.retry_reason == "no_answer"
    reminder = adapter.requests[1].messages[-1]
    assert reminder.role == "user" and SUBMIT in reminder.text()
    assert "{submit_tool}" not in reminder.text()


def test_end_turn_twice_is_no_answer() -> None:
    result, _, _ = _run([_resp("end_turn"), _resp("end_turn")])
    assert result.status == "human_review" and result.reason == "no_answer"


def test_tool_use_without_calls_counts_as_no_answer() -> None:
    result, _, _ = _run([_resp("tool_use"), _resp("end_turn")])
    assert result.reason == "no_answer"


class Boom(RuntimeError):
    pass


def test_api_exception_is_model_error_without_message() -> None:
    def explode(req: ModelRequest) -> ModelResponse:
        raise Boom(SECRET)

    result, _, _ = _run([explode])
    assert result.status == "human_review" and result.reason == "model_error"
    assert result.trace.error_class == "Boom"
    assert SECRET not in result.model_dump_json()


def test_crashing_verifier_is_a_failure_not_a_pass() -> None:
    class BadVerifier(SeenVerifier):
        name = "bad"

        def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
            raise ValueError(SECRET)

    script = [
        tool_call_response([_call("lookup", id=7)], id_prefix="a"),
        tool_call_response([_submit()], id_prefix="b"),
        tool_call_response([_submit()], id_prefix="c"),
    ]
    result, _, _ = _run(script, spec=_spec((SeenVerifier(), BadVerifier())))
    assert result.status == "human_review" and result.reason == "verifier_failed"
    assert [r.passed for r in result.verifier_results] == [True, False]
    assert result.verifier_results[1].errors == ["verifier error (ValueError)"]
    assert SECRET not in result.model_dump_json()


def test_verify_context_carries_the_case_id() -> None:
    seen: list[str] = []

    class CaseVerifier(SeenVerifier):
        def check(self, output: BaseModel, ctx: VerifyContext) -> VerifierResult:
            seen.append(ctx.case_id)
            return super().check(output, ctx)

    script = [tool_call_response([_call("lookup", id=7)]), tool_call_response([_submit()])]
    result, _, _ = _run(script, spec=_spec((CaseVerifier(),)))
    assert result.status == "proposed"
    assert seen == ["C-1"]


# ---- limits -----------------------------------------------------------------------------


def test_limit_steps_after_steps_used() -> None:
    script = [tool_call_response([_call("lookup", id=i)], id_prefix=f"t{i}") for i in range(3)]
    result, _, fake = _run(script, limits=HarnessLimits(max_tool_steps=2))
    assert result.reason == "limit_steps"
    assert result.tool_steps == 2 and len(fake.calls) == 2


def test_limit_steps_turn_exceeding_remaining_runs_none() -> None:
    script = [
        tool_call_response([_call("lookup", id=1)], id_prefix="a"),
        tool_call_response([_call("lookup", id=2), _call("lookup", id=3)], id_prefix="b"),
    ]
    result, _, fake = _run(script, limits=HarnessLimits(max_tool_steps=2))
    assert result.reason == "limit_steps"
    assert fake.calls == [("lookup", {"id": 1})]
    assert result.tool_steps == 1


def test_submit_is_not_a_step() -> None:
    script = [
        tool_call_response([_call("lookup", id=7)], id_prefix="a"),
        tool_call_response([_submit()], id_prefix="b"),
    ]
    result, _, _ = _run(script, limits=HarnessLimits(max_tool_steps=1))
    assert result.status == "proposed" and result.tool_steps == 1


def test_limit_tokens() -> None:
    big = Usage(input_tokens=600, output_tokens=0)
    script = [
        tool_call_response([_call("lookup", id=7)], usage=big, id_prefix="a"),
        tool_call_response([_call("lookup", id=8)], usage=big, id_prefix="b"),
    ]
    result, adapter, _ = _run(script, limits=HarnessLimits(max_total_tokens=1000))
    assert result.reason == "limit_tokens"
    assert len(adapter.requests) == 2
    assert result.totals.total_tokens == 1200


def test_limit_time_with_fake_clock() -> None:
    ticks = iter([0.0, 1.0, 400.0, 400.0])
    script = [tool_call_response([_call("lookup", id=7)]), tool_call_response([_submit()])]
    result, adapter, _ = _run(script, clock=lambda: next(ticks))
    assert result.reason == "limit_time"
    assert len(adapter.requests) == 1
    assert result.wall_ms == 400_000


# ---- tool handling ----------------------------------------------------------------------


def test_repeat_call_returns_cached_content_and_counts_a_step() -> None:
    script = [
        tool_call_response([_call("lookup", id=7)], id_prefix="a"),
        tool_call_response([_call("lookup", id=7)], id_prefix="b"),
        tool_call_response([_submit()], id_prefix="c"),
    ]
    result, adapter, fake = _run(script)
    assert result.status == "proposed"
    assert len(fake.calls) == 1
    assert result.tool_steps == 2
    records = result.trace.tool_calls
    assert [r.repeat for r in records[:2]] == [False, True]
    first = adapter.requests[1].messages[-1].blocks[0]
    second = adapter.requests[2].messages[-1].blocks[0]
    assert isinstance(first, ToolResultBlock) and isinstance(second, ToolResultBlock)
    assert second.content == REPEAT_NOTE + first.content


def test_repeat_of_error_stays_an_error() -> None:
    tools = FakeTools({"lookup:9": _err("not_found")})
    script = [
        tool_call_response([_call("lookup", id=9)], id_prefix="a"),
        tool_call_response([_call("lookup", id=9)], id_prefix="b"),
        tool_call_response([_submit(ids=[])], id_prefix="c"),
    ]
    result, adapter, _ = _run(script, tools=tools)
    assert result.status == "proposed"
    block = adapter.requests[2].messages[-1].blocks[0]
    assert isinstance(block, ToolResultBlock) and block.is_error
    assert result.trace.tool_calls[1].error_code == "not_found"
    assert result.trace.seen_txn_ids == []


def test_parse_error_call_is_not_run_and_counts_a_step() -> None:
    bad = ModelResponse(
        message=Message(
            role="assistant",
            blocks=[ToolCallBlock(id="p0", name="lookup", arguments={}, parse_error="bad json")],
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
        latency_ms=0,
        model="scripted",
        provider="scripted",
    )
    result, adapter, fake = _run([bad, tool_call_response([_submit(ids=[])])])
    assert result.status == "proposed"
    assert fake.calls == [] and result.tool_steps == 1
    block = adapter.requests[1].messages[-1].blocks[0]
    assert isinstance(block, ToolResultBlock) and block.is_error
    assert json.loads(block.content)["code"] == "invalid_argument"


def test_session_dead_is_tool_failure() -> None:
    tools = FakeTools({"lookup:7": _err("internal", dead=True)})
    result, _, _ = _run([tool_call_response([_call("lookup", id=7)])], tools=tools)
    assert result.status == "human_review" and result.reason == "tool_failure"
    assert result.tool_steps == 1


def test_error_outcome_goes_back_as_error() -> None:
    tools = FakeTools({"lookup:9": _err("not_found")})
    script = [tool_call_response([_call("lookup", id=9)]), tool_call_response([_submit(ids=[])])]
    result, adapter, _ = _run(script, tools=tools)
    assert result.status == "proposed"
    block = adapter.requests[1].messages[-1].blocks[0]
    assert isinstance(block, ToolResultBlock) and block.is_error


def test_tool_results_grouped_in_one_message_in_call_order() -> None:
    script = [
        tool_call_response([_call("other", id=3), _call("lookup", id=1), _call("lookup", id=2)]),
        tool_call_response([_submit(ids=[1, 2, 3])], id_prefix="s"),
    ]
    result, adapter, _ = _run(script)
    assert result.status == "proposed"
    reply = adapter.requests[1].messages[-1]
    assert reply.role == "user"
    assert [b.tool_call_id for b in reply.blocks if isinstance(b, ToolResultBlock)] == [
        "call_0",
        "call_1",
        "call_2",
    ]
    assert len(adapter.requests[1].messages) == 3


# ---- schema and verifier retry ----------------------------------------------------------


def test_schema_failure_then_fixed() -> None:
    script = [
        tool_call_response([_call("lookup", id=7)], id_prefix="a"),
        tool_call_response([(SUBMIT, {"verdict": "maybe", "txn_ids": [7]})], id_prefix="b"),
        tool_call_response([_submit()], id_prefix="c"),
    ]
    result, adapter, _ = _run(script)
    assert result.status == "proposed"
    assert result.trace.retry_used and result.trace.retry_reason == "schema_failed"
    block = adapter.requests[2].messages[-1].blocks[0]
    assert isinstance(block, ToolResultBlock) and block.is_error
    errors = json.loads(block.content)["errors"]
    assert errors and errors[0].startswith("verdict: ")
    assert "maybe" not in block.content  # no input values


def test_schema_failure_twice() -> None:
    bad = (SUBMIT, {"verdict": "maybe", "txn_ids": [7]})
    script = [tool_call_response([bad], id_prefix="a"), tool_call_response([bad], id_prefix="b")]
    result, _, _ = _run(script)
    assert result.status == "human_review" and result.reason == "schema_failed"


def test_verifier_fail_then_pass() -> None:
    script = [
        tool_call_response([_submit(ids=[7])], id_prefix="a"),
        tool_call_response([_call("lookup", id=7)], id_prefix="b"),
        tool_call_response([_submit(ids=[7])], id_prefix="c"),
    ]
    result, adapter, _ = _run(script)
    assert result.status == "proposed"
    assert result.trace.retry_reason == "verifier_failed"
    block = adapter.requests[1].messages[-1].blocks[0]
    assert isinstance(block, ToolResultBlock) and block.is_error
    assert "seen: txn 7 not seen" in block.content


def test_verifier_fail_twice() -> None:
    script = [
        tool_call_response([_submit(ids=[7])], id_prefix="a"),
        tool_call_response([_submit(ids=[7])], id_prefix="b"),
    ]
    result, _, _ = _run(script)
    assert result.status == "human_review" and result.reason == "verifier_failed"
    assert [r.passed for r in result.verifier_results] == [False]


def test_retry_is_shared_between_schema_and_no_answer() -> None:
    bad = (SUBMIT, {"verdict": "maybe", "txn_ids": []})
    result, _, _ = _run([tool_call_response([bad]), _resp("end_turn")])
    assert result.status == "human_review" and result.reason == "no_answer"
    assert result.trace.retry_reason == "schema_failed"


def test_two_submits_in_one_turn_is_a_schema_failure() -> None:
    script = [
        tool_call_response([_submit(ids=[]), _submit(ids=[])], id_prefix="a"),
        tool_call_response([_submit(ids=[])], id_prefix="b"),
    ]
    result, _, _ = _run(script)
    assert result.status == "proposed" and result.trace.retry_reason == "schema_failed"


# ---- trace content ----------------------------------------------------------------------


def test_tool_content_kept_or_hashed() -> None:
    script = [tool_call_response([_call("lookup", id=7)]), tool_call_response([_submit()])]
    kept, _, _ = _run(script)
    assert kept.trace.tool_results[0].content == json.dumps({"txn_id": 7, "name": "lookup"})
    hashed, _, _ = _run(script, keep=False)
    assert hashed.trace.tool_results == []
    assert len(hashed.trace.tool_calls[0].content_sha256) == 64
    results = [b for m in hashed.trace.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert results and all(b.content.startswith("sha256:") for b in results)


def test_seen_txn_ids_extraction() -> None:
    data = {
        "txn_id": 5,
        "items": [{"txn_id": 3, "nested": {"txn_id": 9}}, {"txn_id": True}],
        "match": {"txn_ids": [4, 2, "x"]},
        "other_id": 100,
    }
    found: set[int] = set()
    collect_txn_ids(data, found)
    assert found == {2, 3, 4, 5, 9}
    assert txn_ids_in_content("not json") == set()
    tools = FakeTools({"lookup:1": _ok(data), "other:2": _err("not_found")})
    script = [
        tool_call_response([_call("lookup", id=1), _call("other", id=2)]),
        tool_call_response([_submit(ids=[9])], id_prefix="s"),
    ]
    result, _, _ = _run(script, tools=tools)
    assert result.trace.seen_txn_ids == [2, 3, 4, 5, 9]


# ---- invariants -------------------------------------------------------------------------


def _trace() -> RunTrace:
    result, _, _ = _run([_resp("refusal")])
    return result.trace


def _case(**overrides: Any) -> CaseResult:
    base: dict[str, Any] = {
        "case_id": "C-1",
        "status": "proposed",
        "reason": None,
        "findings": {"verdict": "yes"},
        "verifier_results": [VerifierResult(name="v", passed=True, errors=[])],
        "totals": Usage(input_tokens=0, output_tokens=0),
        "cost_usd": "0",
        "tool_steps": 0,
        "wall_ms": 0,
        "trace": _trace(),
    }
    return CaseResult.model_validate({**base, **overrides})


def test_case_result_invariants() -> None:
    assert _case().status == "proposed"
    with pytest.raises(ValidationError):
        _case(findings=None)
    with pytest.raises(ValidationError):
        _case(verifier_results=[])
    with pytest.raises(ValidationError):
        _case(reason="refusal")
    with pytest.raises(ValidationError):
        _case(verifier_results=[VerifierResult(name="v", passed=False, errors=["e"])])
    with pytest.raises(ValidationError):
        _case(status="human_review", reason=None)
    with pytest.raises(ValidationError):
        _case(status="closed")
    assert _case(status="human_review", reason="refusal", findings=None).reason == "refusal"


def test_trace_rejects_unsorted_seen_ids() -> None:
    data = _trace().model_dump()
    data["seen_txn_ids"] = [3, 1]
    with pytest.raises(ValidationError):
        RunTrace.model_validate(data)


def test_text_block_case_message_is_first() -> None:
    result, _, _ = _run([_resp("refusal")])
    first = result.trace.messages[0]
    assert first.role == "user" and isinstance(first.blocks[0], TextBlock)


# ---- review fixes -----------------------------------------------------------------------


def test_zero_verifiers_never_proposes() -> None:
    result, _, _ = _run([tool_call_response([_submit()])], spec=_spec(verifiers=()))
    assert result.status == "human_review" and result.reason == "internal_error"
    assert result.verifier_results == []
    assert not result.trace.retry_used


def test_submit_turn_crossing_token_budget_is_limit_tokens() -> None:
    big = Usage(input_tokens=2000, output_tokens=0)
    result, adapter, _ = _run(
        [tool_call_response([_submit()], usage=big)],
        limits=HarnessLimits(max_total_tokens=1000),
    )
    assert result.status == "human_review" and result.reason == "limit_tokens"
    assert len(adapter.requests) == 1


class SlowTools(FakeTools):
    def __init__(self, now: list[float]) -> None:
        super().__init__()
        self.now = now

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.now[0] += 400.0
        return await super().call(name, arguments)


def test_slow_tool_turn_crossing_clock_is_limit_time() -> None:
    now = [0.0]
    tools = SlowTools(now)
    result, adapter, _ = _run(
        [tool_call_response([_call("lookup", id=7), _submit()])],
        tools=tools,
        clock=lambda: now[0],
    )
    assert result.status == "human_review" and result.reason == "limit_time"
    assert len(adapter.requests) == 1 and len(tools.calls) == 1


def test_duplicate_call_ids_are_model_error_and_run_nothing() -> None:
    dup = ModelResponse(
        message=Message(
            role="assistant",
            blocks=[
                ToolCallBlock(id="same", name="lookup", arguments={"id": 1}),
                ToolCallBlock(id="same", name="other", arguments={"id": 2}),
            ],
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
        latency_ms=0,
        model="scripted",
        provider="scripted",
    )
    result, _, fake = _run([dup])
    assert result.status == "human_review" and result.reason == "model_error"
    assert fake.calls == [] and result.tool_steps == 0
