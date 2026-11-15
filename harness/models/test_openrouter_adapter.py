import asyncio
from decimal import Decimal
from typing import Any, cast

import pytest
from openai import AsyncOpenAI
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.completion_usage import PromptTokensDetails

from harness.models.config import ModelProfile, Prices, ProviderRouting, cost_usd
from harness.models.openrouter_adapter import (
    MissingApiKeyError,
    OpenRouterAdapter,
    from_openrouter_response,
    to_openrouter_request,
)
from harness.models.types import (
    EMPTY_TEXT_PLACEHOLDER,
    Message,
    ModelRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
)

MODEL = "meta-llama/llama-3.3-70b-instruct"


def _profile(routing: ProviderRouting | None = None) -> ModelProfile:
    return ModelProfile(
        name="or-test",
        provider="openrouter",
        model=MODEL,
        max_output_tokens=8000,
        provider_routing=routing,
        prices=Prices(
            input=Decimal("0.60"),
            output=Decimal("0.80"),
            cache_read=Decimal("0.10"),
            cache_write=Decimal("0.70"),
        ),
    )


def _call(id_: str, name: str, arguments: str) -> ChatCompletionMessageFunctionToolCall:
    return ChatCompletionMessageFunctionToolCall(
        id=id_, type="function", function=Function(name=name, arguments=arguments)
    )


def _completion(
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageFunctionToolCall] | None = None,
    finish_reason: Any = "stop",
    usage: CompletionUsage | None = None,
) -> ChatCompletion:
    return ChatCompletion(
        id="gen-1",
        object="chat.completion",
        created=0,
        model=MODEL,
        choices=[
            Choice(
                index=0,
                finish_reason=finish_reason,
                message=ChatCompletionMessage(
                    role="assistant", content=content, tool_calls=cast(Any, tool_calls)
                ),
            )
        ],
        usage=usage,
    )


def test_request_shape() -> None:
    req = ModelRequest(
        system="You are an analyst.",
        messages=[
            Message(role="user", blocks=[TextBlock(text="Investigate alert 7")]),
            Message(
                role="assistant",
                blocks=[
                    TextBlock(text="Looking"),
                    ToolCallBlock(id="c1", name="get_alert", arguments={"z": 1, "a": 2}),
                    ToolCallBlock(id="c2", name="get_party", arguments={}),
                ],
            ),
            Message(
                role="user",
                blocks=[
                    ToolResultBlock(tool_call_id="c1", content='{"id": 7}'),
                    ToolResultBlock(tool_call_id="c2", content="not found", is_error=True),
                ],
            ),
        ],
        tools=[ToolSpec(name="get_alert", description="d", input_schema={"type": "object"})],
        max_output_tokens=4000,
    )
    kw = to_openrouter_request(req, _profile())
    assert kw["model"] == MODEL
    assert kw["max_tokens"] == 4000
    assert kw["tool_choice"] == "auto"
    assert "extra_body" not in kw
    assert kw["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_alert",
                "description": "d",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert kw["messages"] == [
        {"role": "system", "content": "You are an analyst."},
        {"role": "user", "content": "Investigate alert 7"},
        {
            "role": "assistant",
            "content": "Looking",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_alert", "arguments": '{"a": 2, "z": 1}'},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "get_party", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"id": 7}'},
        {"role": "tool", "tool_call_id": "c2", "content": "ERROR: not found"},
    ]


def test_provider_routing_in_extra_body() -> None:
    routing = ProviderRouting(order=["together", "fireworks"], allow_fallbacks=False)
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="hi")])],
        tools=[],
        max_output_tokens=10,
    )
    kw = to_openrouter_request(req, _profile(routing))
    assert kw["extra_body"] == {
        "provider": {"order": ["together", "fireworks"], "allow_fallbacks": False}
    }
    assert "tools" not in kw and "tool_choice" not in kw


def test_tool_calls_parsed() -> None:
    resp = from_openrouter_response(
        _completion(
            content="ok",
            tool_calls=[_call("c1", "get_alert", '{"alert_id": 7}')],
            finish_reason="tool_calls",
        ),
        _profile(),
        latency_ms=5,
    )
    assert resp.stop_reason == "tool_use"
    assert resp.provider == "openrouter"
    assert resp.request_id == "gen-1"
    assert resp.message.provider_raw is None
    assert resp.message.blocks == [
        TextBlock(text="ok"),
        ToolCallBlock(id="c1", name="get_alert", arguments={"alert_id": 7}),
    ]


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"text"'])
def test_malformed_arguments_keep_id(bad: str) -> None:
    resp = from_openrouter_response(
        _completion(tool_calls=[_call("c9", "get_alert", bad)], finish_reason="tool_calls"),
        _profile(),
        latency_ms=0,
    )
    (block,) = resp.message.tool_calls()
    assert block.id == "c9"
    assert block.arguments == {}
    assert block.parse_error


def test_empty_arguments_are_empty_object() -> None:
    resp = from_openrouter_response(
        _completion(tool_calls=[_call("c1", "list", "")], finish_reason="tool_calls"),
        _profile(),
        latency_ms=0,
    )
    assert resp.message.tool_calls()[0].parse_error is None


@pytest.mark.parametrize(
    ("raw", "neutral"),
    [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "refusal"),
        ("function_call", "other"),
    ],
)
def test_finish_reason_mapping(raw: str, neutral: str) -> None:
    resp = from_openrouter_response(_completion(finish_reason=raw), _profile(), latency_ms=0)
    assert resp.stop_reason == neutral
    assert resp.message.blocks == [TextBlock(text="")]


def test_empty_assistant_turn_gets_placeholder() -> None:
    req = ModelRequest(
        system="s",
        messages=[
            Message(role="user", blocks=[TextBlock(text="go")]),
            Message(role="assistant", blocks=[TextBlock(text="")]),
            Message(
                role="assistant",
                blocks=[ToolCallBlock(id="c1", name="get_alert", arguments={})],
            ),
        ],
        tools=[],
        max_output_tokens=10,
    )
    msgs = to_openrouter_request(req, _profile())["messages"]
    assert msgs[2] == {"role": "assistant", "content": EMPTY_TEXT_PLACEHOLDER}
    assert msgs[3]["content"] is None and msgs[3]["tool_calls"]


def test_tool_calls_with_stop_mean_tool_use() -> None:
    resp = from_openrouter_response(
        _completion(tool_calls=[_call("c1", "get_alert", "{}")], finish_reason="stop"),
        _profile(),
        latency_ms=0,
    )
    assert resp.stop_reason == "tool_use"


def test_usage_and_cost() -> None:
    usage = CompletionUsage(
        prompt_tokens=10_000,
        completion_tokens=500,
        total_tokens=10_500,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=6000, cache_write_tokens=1000),
    )
    resp = from_openrouter_response(_completion(content="x", usage=usage), _profile(), latency_ms=0)
    assert resp.usage.input_tokens == 3000
    assert resp.usage.cache_read_tokens == 6000
    assert resp.usage.cache_write_tokens == 1000
    assert resp.usage.output_tokens == 500
    # 3000*0.60 + 500*0.80 + 6000*0.10 + 1000*0.70 = 3500 per million
    assert cost_usd(_profile(), resp.usage) == Decimal("0.0035")


def test_usage_without_details() -> None:
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=7, total_tokens=107)
    resp = from_openrouter_response(_completion(content="x", usage=usage), _profile(), 0)
    assert (resp.usage.input_tokens, resp.usage.cache_read_tokens) == (100, 0)


def test_missing_api_key_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    adapter = OpenRouterAdapter(_profile())
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="hi")])],
        tools=[],
        max_output_tokens=10,
    )
    with pytest.raises(MissingApiKeyError) as info:
        asyncio.run(adapter.complete(req))
    assert "OPENROUTER_API_KEY" in str(info.value)


class _FakeCompletions:
    def __init__(self, reply: ChatCompletion) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        return self.reply


class _FakeChat:
    def __init__(self, reply: ChatCompletion) -> None:
        self.completions = _FakeCompletions(reply)


class _FakeClient:
    def __init__(self, reply: ChatCompletion) -> None:
        self.chat = _FakeChat(reply)


def test_complete_with_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = _FakeClient(_completion(content="done"))
    routing = ProviderRouting(only=["together"], allow_fallbacks=False)
    adapter = OpenRouterAdapter(_profile(routing), client=cast(AsyncOpenAI, fake))
    req = ModelRequest(
        system="s",
        messages=[Message(role="user", blocks=[TextBlock(text="hi")])],
        tools=[],
        max_output_tokens=10,
    )
    resp = asyncio.run(adapter.complete(req))
    (call,) = fake.chat.completions.calls
    assert call["extra_body"] == {"provider": {"only": ["together"], "allow_fallbacks": False}}
    assert resp.stop_reason == "end_turn"
    assert resp.message.text() == "done"
