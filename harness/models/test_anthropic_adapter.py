import asyncio
from decimal import Decimal
from typing import Any, cast

import pytest
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import (
    RedactedThinkingBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from anthropic.types import (
    TextBlock as AnthropicText,
)
from anthropic.types import Usage as AnthropicUsage

from harness.models.anthropic_adapter import (
    AnthropicAdapter,
    from_anthropic_response,
    to_anthropic_request,
)
from harness.models.config import ModelProfile, Prices, cost_usd
from harness.models.types import (
    EMPTY_TEXT_PLACEHOLDER,
    Message,
    ModelRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
)

MODEL = "claude-opus-5-5"


def _profile(effort: Any = "medium") -> ModelProfile:
    return ModelProfile(
        name="claude-test",
        provider="anthropic",
        model=MODEL,
        max_output_tokens=16_000,
        effort=effort,
        prices=Prices(
            input=Decimal("4.00"),
            output=Decimal("20.00"),
            cache_read=Decimal("0.20"),
            cache_write=Decimal("5.00"),
        ),
    )


def _tools() -> list[ToolSpec]:
    return [
        ToolSpec(name="get_alert", description="Get one alert", input_schema={"type": "object"}),
        ToolSpec(name="submit_findings", description="Submit", input_schema={"type": "object"}),
    ]


def _message(
    content: list[Any],
    stop_reason: Any = "end_turn",
    usage: AnthropicUsage | None = None,
) -> AnthropicMessage:
    msg = AnthropicMessage(
        id="msg_1",
        content=content,
        model=MODEL,
        role="assistant",
        stop_reason=stop_reason,
        type="message",
        usage=usage or AnthropicUsage(input_tokens=10, output_tokens=5),
    )
    msg._request_id = "req_123"
    return msg


def _thinking_turn() -> AnthropicMessage:
    return _message(
        [
            ThinkingBlock(type="thinking", thinking="look at the alert", signature="sig==abc"),
            RedactedThinkingBlock(type="redacted_thinking", data="opaque"),
            AnthropicText(type="text", text="Checking."),
            ToolUseBlock(type="tool_use", id="toolu_1", name="get_alert", input={"alert_id": 7}),
        ],
        stop_reason="tool_use",
    )


def test_request_shape_cache_thinking_effort() -> None:
    req = ModelRequest(
        system="You are an analyst.",
        messages=[Message(role="user", blocks=[TextBlock(text="Investigate alert 7")])],
        tools=_tools(),
        max_output_tokens=4000,
    )
    kw = to_anthropic_request(req, _profile())
    assert kw["model"] == MODEL
    assert kw["max_tokens"] == 4000
    assert kw["system"] == [
        {"type": "text", "text": "You are an analyst.", "cache_control": {"type": "ephemeral"}}
    ]
    assert [t["name"] for t in kw["tools"]] == ["get_alert", "submit_findings"]
    assert "cache_control" not in kw["tools"][0]
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["tool_choice"] == {"type": "auto"}
    assert kw["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Investigate alert 7"}]}
    ]


def test_no_effort_means_no_output_config() -> None:
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="hi")])],
        tools=_tools(),
        max_output_tokens=100,
    )
    assert "output_config" not in to_anthropic_request(req, _profile(effort=None))


def test_provider_raw_replayed_unchanged_with_thinking_signature() -> None:
    resp = from_anthropic_response(_thinking_turn(), _profile(), latency_ms=12)
    raw = resp.message.provider_raw
    assert isinstance(raw, list)
    assert raw[0] == {"type": "thinking", "thinking": "look at the alert", "signature": "sig==abc"}
    assert raw[1] == {"type": "redacted_thinking", "data": "opaque"}
    follow_up = Message(
        role="user",
        blocks=[ToolResultBlock(tool_call_id="toolu_1", content='{"id": 7}')],
    )
    req = ModelRequest(
        system="s",
        messages=[
            Message(role="user", blocks=[TextBlock(text="go")]),
            resp.message,
            follow_up,
        ],
        tools=_tools(),
        max_output_tokens=100,
    )
    kw = to_anthropic_request(req, _profile())
    assert kw["messages"][1] == {"role": "assistant", "content": raw}


def test_empty_assistant_text_gets_placeholder() -> None:
    req = ModelRequest(
        system="s",
        messages=[
            Message(role="user", blocks=[TextBlock(text="go")]),
            Message(role="assistant", blocks=[TextBlock(text="")]),
            Message(role="user", blocks=[TextBlock(text="again")]),
        ],
        tools=[],
        max_output_tokens=100,
    )
    kw = to_anthropic_request(req, _profile())
    assert kw["messages"][1]["content"] == [{"type": "text", "text": EMPTY_TEXT_PLACEHOLDER}]


def test_assistant_without_raw_built_from_blocks() -> None:
    assistant = Message(
        role="assistant",
        blocks=[
            TextBlock(text="Looking"),
            ToolCallBlock(id="c1", name="get_alert", arguments={"alert_id": 1}),
        ],
        provider_raw=[{"type": "text", "text": "ignored"}],
        provider="openrouter",
    )
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="go")]), assistant],
        tools=[],
        max_output_tokens=100,
    )
    kw = to_anthropic_request(req, _profile())
    assert kw["messages"][1]["content"] == [
        {"type": "text", "text": "Looking"},
        {"type": "tool_use", "id": "c1", "name": "get_alert", "input": {"alert_id": 1}},
    ]
    assert "tools" not in kw and "tool_choice" not in kw


def test_tool_results_grouped_in_one_user_message() -> None:
    user = Message(
        role="user",
        blocks=[
            ToolResultBlock(tool_call_id="a", content='{"ok": 1}'),
            TextBlock(text="note"),
            ToolResultBlock(tool_call_id="b", content='{"code": "not_found"}', is_error=True),
        ],
    )
    req = ModelRequest(system="s", messages=[user], tools=[], max_output_tokens=100)
    kw = to_anthropic_request(req, _profile())
    assert len(kw["messages"]) == 1
    assert kw["messages"][0]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": '{"ok": 1}', "is_error": False},
        {
            "type": "tool_result",
            "tool_use_id": "b",
            "content": '{"code": "not_found"}',
            "is_error": True,
        },
        {"type": "text", "text": "note"},
    ]


def test_response_blocks_and_request_id() -> None:
    resp = from_anthropic_response(_thinking_turn(), _profile(), latency_ms=12)
    assert resp.provider == "anthropic"
    assert resp.message.provider == "anthropic"
    assert resp.request_id == "req_123"
    assert resp.latency_ms == 12
    assert resp.model == MODEL
    assert resp.stop_reason == "tool_use"
    assert resp.message.blocks == [
        TextBlock(text="Checking."),
        ToolCallBlock(id="toolu_1", name="get_alert", arguments={"alert_id": 7}),
    ]


@pytest.mark.parametrize(
    ("raw", "neutral"),
    [
        ("end_turn", "end_turn"),
        ("tool_use", "tool_use"),
        ("max_tokens", "max_tokens"),
        ("refusal", "refusal"),
        ("pause_turn", "other"),
        ("stop_sequence", "other"),
        ("model_context_window_exceeded", "max_tokens"),
        (None, "other"),
    ],
)
def test_stop_reason_mapping(raw: Any, neutral: str) -> None:
    resp = from_anthropic_response(_message([], stop_reason=raw), _profile(), latency_ms=0)
    assert resp.stop_reason == neutral
    assert resp.message.blocks == [TextBlock(text="")]


def test_usage_and_cost() -> None:
    usage = AnthropicUsage(
        input_tokens=1000,
        output_tokens=500,
        cache_read_input_tokens=20_000,
        cache_creation_input_tokens=3000,
    )
    resp = from_anthropic_response(
        _message([AnthropicText(type="text", text="x")], usage=usage), _profile(), latency_ms=0
    )
    assert resp.usage.input_tokens == 1000
    assert resp.usage.output_tokens == 500
    assert resp.usage.cache_read_tokens == 20_000
    assert resp.usage.cache_write_tokens == 3000
    # 1000*4 + 500*20 + 20000*0.20 + 3000*5 = 33000 per million
    assert cost_usd(_profile(), resp.usage) == Decimal("0.033")


class _FakeMessages:
    def __init__(self, reply: AnthropicMessage) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> AnthropicMessage:
        self.calls.append(kwargs)
        return self.reply


class _FakeClient:
    def __init__(self, reply: AnthropicMessage) -> None:
        self.messages = _FakeMessages(reply)


def test_complete_with_fake_client() -> None:
    fake = _FakeClient(_thinking_turn())
    adapter = AnthropicAdapter(_profile(), client=cast(AsyncAnthropic, fake))
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="go")])],
        tools=_tools(),
        max_output_tokens=100,
    )
    resp = asyncio.run(adapter.complete(req))
    assert fake.messages.calls == [to_anthropic_request(req, _profile())]
    assert resp.stop_reason == "tool_use"
    assert resp.latency_ms >= 0


def test_rejects_wrong_provider() -> None:
    profile = ModelProfile(
        name="x",
        provider="scripted",
        model="scripted",
        max_output_tokens=10,
        prices=_profile().prices,
    )
    with pytest.raises(ValueError):
        AnthropicAdapter(profile)
