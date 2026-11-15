"""OpenRouter adapter through the OpenAI SDK chat.completions endpoint.

Confirmed API facts (checked against the installed openai==3.42.0 package, in the
openai.types modules, not from memory):
- AsyncOpenAI(base_url=..., api_key=...) and chat.completions.create take model,
  messages, tools, tool_choice, max_tokens (max_completion_tokens also exists; we use
  max_tokens, which OpenRouter documents) and extra_body for fields the SDK does not
  know, such as OpenRouter's provider routing ({"provider": {...}}).
- Tools use the function format {"type": "function", "function": {"name",
  "description", "parameters"}}.
- Assistant message params carry tool_calls [{"id", "type": "function", "function":
  {"name", "arguments"}}] with arguments as a JSON string. Tool results are separate
  {"role": "tool", "tool_call_id", "content"} messages with no is_error field, so an
  error result is sent with an "ERROR: " prefix on its content.
- Response choices[0].message.tool_calls is a union of function tool calls (id,
  function.name, function.arguments as a JSON string) and custom tool calls (id,
  custom.name, custom.input). We never send custom tools; one that arrives anyway is
  kept by id with a parse_error.
- finish_reason values: stop, length, tool_calls, content_filter and function_call
  (the last one is legacy and maps to "other").
- usage: prompt_tokens, completion_tokens, total_tokens and an optional
  prompt_tokens_details with cached_tokens and also cache_write_tokens (not in the
  plan). prompt_tokens includes both, so both are subtracted to get plain input.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from harness.models.config import ModelProfile
from harness.models.types import (
    EMPTY_TEXT_PLACEHOLDER,
    Message,
    ModelRequest,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
)

PROVIDER = "openrouter"
BASE_URL = "https://openrouter.ai/api/v1"
API_KEY_ENV = "OPENROUTER_API_KEY"

_FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class MissingApiKeyError(RuntimeError):
    """The OpenRouter API key env var is not set."""


def _assistant_message(message: Message) -> dict[str, Any]:
    calls = message.tool_calls()
    # Content may be None only when tool_calls carry the turn.
    text = message.text() or (None if calls else EMPTY_TEXT_PLACEHOLDER)
    out: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments, sort_keys=True)},
            }
            for c in calls
        ]
    return out


def _user_messages(message: Message) -> list[dict[str, Any]]:
    # Tool messages must directly follow the assistant turn that asked for them,
    # so they come before any user text from the same neutral message.
    out: list[dict[str, Any]] = [
        {
            "role": "tool",
            "tool_call_id": b.tool_call_id,
            "content": f"ERROR: {b.content}" if b.is_error else b.content,
        }
        for b in message.blocks
        if isinstance(b, ToolResultBlock)
    ]
    text = message.text()
    if text:
        out.append({"role": "user", "content": text})
    return out


def to_openrouter_request(req: ModelRequest, profile: ModelProfile) -> dict[str, Any]:
    """Build the keyword arguments for chat.completions.create from a neutral request."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": req.system}]
    for m in req.messages:
        if m.role == "assistant":
            messages.append(_assistant_message(m))
        else:
            messages.extend(_user_messages(m))
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "max_tokens": req.max_output_tokens,
    }
    if req.tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in req.tools
        ]
        kwargs["tool_choice"] = "auto"
    if profile.provider_routing is not None:
        routing = profile.provider_routing.model_dump(exclude_none=True)
        if routing:
            kwargs["extra_body"] = {"provider": routing}
    return kwargs


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return {}, f"arguments are not valid JSON: {exc.msg}"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be a JSON object, got {type(parsed).__name__}"
    return parsed, None


def from_openrouter_response(
    resp: ChatCompletion, profile: ModelProfile, latency_ms: int
) -> ModelResponse:
    """Convert an SDK ChatCompletion into a neutral ModelResponse."""
    if not resp.choices:
        raise ValueError("OpenRouter response has no choices")
    choice = resp.choices[0]
    msg = choice.message
    blocks: list[TextBlock | ToolCallBlock] = []
    if msg.content:
        blocks.append(TextBlock(text=msg.content))
    for call in msg.tool_calls or []:
        if call.type == "function":
            args, error = _parse_arguments(call.function.arguments)
            blocks.append(
                ToolCallBlock(
                    id=call.id, name=call.function.name, arguments=args, parse_error=error
                )
            )
        else:
            blocks.append(
                ToolCallBlock(
                    id=call.id,
                    name=call.custom.name,
                    arguments={},
                    parse_error="custom tool calls are not supported",
                )
            )
    has_calls = any(isinstance(b, ToolCallBlock) for b in blocks)
    if not blocks:
        blocks.append(TextBlock(text=msg.refusal or ""))
    stop = _FINISH_REASONS.get(choice.finish_reason, "other")
    if has_calls and stop == "end_turn":
        stop = "tool_use"
    usage = _usage(resp)
    return ModelResponse(
        message=Message(role="assistant", blocks=list(blocks)),
        stop_reason=stop,
        usage=usage,
        latency_ms=latency_ms,
        model=resp.model,
        provider=PROVIDER,
        request_id=resp.id,
    )


def _usage(resp: ChatCompletion) -> Usage:
    u = resp.usage
    if u is None:
        return Usage(input_tokens=0, output_tokens=0)
    details = u.prompt_tokens_details
    cached = (details.cached_tokens or 0) if details else 0
    written = (details.cache_write_tokens or 0) if details else 0
    return Usage(
        input_tokens=max(u.prompt_tokens - cached - written, 0),
        output_tokens=u.completion_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=written,
    )


class OpenRouterAdapter:
    def __init__(self, profile: ModelProfile, client: AsyncOpenAI | None = None) -> None:
        if profile.provider != PROVIDER:
            raise ValueError(f"profile {profile.name!r} is not an openrouter profile")
        self.profile = profile
        self._client = client

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            key = os.environ.get(API_KEY_ENV)
            if not key:
                raise MissingApiKeyError(f"{API_KEY_ENV} is not set")
            self._client = AsyncOpenAI(base_url=BASE_URL, api_key=key)
        return self._client

    async def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        kwargs = to_openrouter_request(req, self.profile)
        start = time.perf_counter()
        resp = await client.chat.completions.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if not isinstance(resp, ChatCompletion):
            raise TypeError("expected a non-streaming ChatCompletion from chat.completions.create")
        return from_openrouter_response(resp, self.profile, latency_ms)
