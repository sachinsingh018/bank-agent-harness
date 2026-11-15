import pytest
from pydantic import TypeAdapter, ValidationError

from harness.models.types import (
    Block,
    Message,
    ModelRequest,
    ModelResponse,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    Usage,
)


def _message() -> Message:
    return Message(
        role="assistant",
        blocks=[
            TextBlock(text="checking"),
            ToolCallBlock(id="c1", name="get_alert", arguments={"alert_id": 7, "x": [1, "a"]}),
        ],
        provider_raw=[{"type": "thinking", "thinking": "hmm", "signature": "sig"}],
        provider="anthropic",
    )


def test_block_union_round_trip_json() -> None:
    msg = _message()
    again = Message.model_validate_json(msg.model_dump_json())
    assert again == msg
    assert [type(b) for b in again.blocks] == [TextBlock, ToolCallBlock]
    assert again.provider_raw == msg.provider_raw
    result = Message(
        role="user",
        blocks=[ToolResultBlock(tool_call_id="c1", content='{"ok": true}', is_error=True)],
    )
    assert Message.model_validate_json(result.model_dump_json()) == result


def test_block_discriminator_picks_type() -> None:
    adapter: TypeAdapter[Block] = TypeAdapter(Block)
    block = adapter.validate_python({"type": "tool_result", "tool_call_id": "c", "content": "x"})
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is False
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "image", "url": "x"})


def test_unknown_block_field_rejected() -> None:
    with pytest.raises(ValidationError):
        TextBlock.model_validate({"type": "text", "text": "a", "extra": 1})


def test_message_helpers() -> None:
    msg = _message()
    assert msg.text() == "checking"
    assert [c.id for c in msg.tool_calls()] == ["c1"]


def test_usage_addition() -> None:
    a = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=3, cache_write_tokens=2)
    b = Usage(input_tokens=1, output_tokens=2)
    total = a + b
    assert total == Usage(
        input_tokens=11, output_tokens=7, cache_read_tokens=3, cache_write_tokens=2
    )
    assert total.total_tokens == 23


def test_usage_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=0)


def test_request_and_response_round_trip() -> None:
    req = ModelRequest(
        system="sys",
        messages=[Message(role="user", blocks=[TextBlock(text="hi")])],
        tools=[ToolSpec(name="t", description="d", input_schema={"type": "object"})],
        max_output_tokens=100,
    )
    assert ModelRequest.model_validate_json(req.model_dump_json()) == req
    resp = ModelResponse(
        message=_message(),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
        latency_ms=12,
        model="m",
        provider="scripted",
        request_id="r1",
    )
    assert ModelResponse.model_validate_json(resp.model_dump_json()) == resp


def test_bad_stop_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelResponse(
            message=_message(),
            stop_reason="stop",  # type: ignore[arg-type]
            usage=Usage(input_tokens=1, output_tokens=1),
            latency_ms=0,
            model="m",
            provider="p",
            request_id=None,
        )


def test_message_needs_blocks() -> None:
    with pytest.raises(ValidationError, match="at least one block"):
        Message(role="user", blocks=[])


def test_tool_call_only_in_assistant() -> None:
    with pytest.raises(ValidationError, match="tool_call blocks"):
        Message(role="user", blocks=[ToolCallBlock(id="c", name="t", arguments={})])


def test_tool_result_only_in_user() -> None:
    with pytest.raises(ValidationError, match="tool_result blocks"):
        Message(role="assistant", blocks=[ToolResultBlock(tool_call_id="c", content="x")])
    ok = Message(role="user", blocks=[ToolResultBlock(tool_call_id="c", content="x")])
    assert ok.role == "user"


def test_provider_required_with_provider_raw() -> None:
    with pytest.raises(ValidationError, match="provider is required"):
        Message(role="assistant", blocks=[TextBlock(text="a")], provider_raw={"k": 1})
    msg = Message(role="assistant", blocks=[TextBlock(text="a")], provider="openrouter")
    assert msg.provider_raw is None


def test_tool_call_parse_error_keeps_id() -> None:
    block = ToolCallBlock(id="c9", name="t", arguments={}, parse_error="not a JSON object")
    again = ToolCallBlock.model_validate_json(block.model_dump_json())
    assert again.id == "c9" and again.arguments == {} and again.parse_error == block.parse_error
