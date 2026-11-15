import asyncio

import pytest

from harness.models.scripted_adapter import (
    ScriptedAdapter,
    ScriptExhaustedError,
    text_response,
    tool_call_response,
)
from harness.models.types import (
    Message,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    TextBlock,
    ToolCallBlock,
)


def _req(text: str) -> ModelRequest:
    return ModelRequest(
        system="sys",
        messages=[Message(role="user", blocks=[TextBlock(text=text)])],
        tools=[],
        max_output_tokens=100,
    )


def test_returns_in_order_and_records_requests() -> None:
    def echo(req: ModelRequest) -> ModelResponse:
        return text_response("echo " + req.messages[-1].text())

    adapter = ScriptedAdapter(
        [tool_call_response([("get_alert", {"alert_id": 1})]), echo, text_response("done")]
    )
    first = asyncio.run(adapter.complete(_req("a")))
    second = asyncio.run(adapter.complete(_req("b")))
    third = asyncio.run(adapter.complete(_req("c")))
    assert first.stop_reason == "tool_use"
    assert first.message.tool_calls()[0] == ToolCallBlock(
        id="call_0", name="get_alert", arguments={"alert_id": 1}
    )
    assert second.message.text() == "echo b"
    assert third.message.text() == "done" and third.stop_reason == "end_turn"
    assert [r.messages[-1].text() for r in adapter.requests] == ["a", "b", "c"]
    assert adapter.remaining == 0


def test_recorded_request_is_deep_copy() -> None:
    adapter = ScriptedAdapter([text_response("x")])
    req = _req("original")
    asyncio.run(adapter.complete(req))
    req.messages.append(Message(role="user", blocks=[TextBlock(text="later")]))
    block = req.messages[0].blocks[0]
    assert isinstance(block, TextBlock)
    block.text = "mutated"
    recorded = adapter.requests[0]
    assert len(recorded.messages) == 1
    assert recorded.messages[0].text() == "original"


def test_exhaustion_raises_clear_error() -> None:
    adapter = ScriptedAdapter([text_response("only")])
    asyncio.run(adapter.complete(_req("a")))
    with pytest.raises(ScriptExhaustedError, match="ran out of responses after 1"):
        asyncio.run(adapter.complete(_req("b")))
    assert len(adapter.requests) == 2


def test_tool_call_response_with_text_and_parallel_calls() -> None:
    resp = tool_call_response([("a", {}), ("b", {"k": "v"})], text="thinking aloud")
    assert resp.message.text() == "thinking aloud"
    assert [c.name for c in resp.message.tool_calls()] == ["a", "b"]
    assert [c.id for c in resp.message.tool_calls()] == ["call_0", "call_1"]


def test_satisfies_protocol() -> None:
    adapter: ModelAdapter = ScriptedAdapter([])
    assert adapter.profile.provider == "scripted"
