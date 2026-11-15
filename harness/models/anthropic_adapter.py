"""Anthropic Messages API adapter (our own loop, no SDK tool runner).

Confirmed API facts (checked against the installed anthropic==2.3.0 package, in the
anthropic.types modules, not from memory):
- messages.create takes model, max_tokens, messages, system (str or a list of text
  blocks, each with an optional cache_control), tools (name, description,
  input_schema, optional cache_control), tool_choice ({"type": "auto"} and others),
  thinking and output_config. Both thinking and output_config are typed params in
  this version, so no extra_body is needed.
- thinking accepts {"type": "adaptive"} (plus "enabled", "disabled" and a new
  "between_tools"). Adaptive also takes an optional display ("summarized" or "omitted").
- output_config is {"effort": "low" | "medium" | "high" | "xhigh" | "max", "format": ...}.
- cache_control is {"type": "ephemeral", "ttl": "5m" | "1h"}. There is also a new
  top-level cache_control param that marks the last cacheable block; we do not use it
  and set explicit breakpoints instead.
- Response content blocks include text, thinking (thinking, signature),
  redacted_thinking (data) and tool_use (id, name, input, optional caller and
  toolset_name), plus server tool blocks we never request.
- stop_reason values: end_turn, max_tokens, stop_sequence, tool_use, pause_turn,
  refusal and model_context_window_exceeded (the last one is not in the plan; it is
  a limit, so it maps to "max_tokens"). A refusal also carries Message.stop_details with a category.
- usage: input_tokens, output_tokens, cache_read_input_tokens and
  cache_creation_input_tokens (both optional, may be None).
- The top-level response object has _request_id (from the request-id header).
- Every response block field is accepted back by the matching *BlockParam type, so a
  model_dump(mode="json", exclude_none=True) of each block can be replayed unchanged.
"""

from __future__ import annotations

import time
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage

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

PROVIDER = "anthropic"

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    # Context window full is a limit, like max_tokens, so the loop reports limit_output.
    "model_context_window_exceeded": "max_tokens",
}


def _user_content(message: Message) -> list[dict[str, Any]]:
    # Tool results go first: the API expects them at the start of the user turn.
    results = [
        {
            "type": "tool_result",
            "tool_use_id": b.tool_call_id,
            "content": b.content,
            "is_error": b.is_error,
        }
        for b in message.blocks
        if isinstance(b, ToolResultBlock)
    ]
    texts = [
        {"type": "text", "text": b.text}
        for b in message.blocks
        if isinstance(b, TextBlock) and b.text
    ]
    return results + texts


def _assistant_content(message: Message) -> list[dict[str, Any]]:
    if message.provider == PROVIDER and message.provider_raw is not None:
        raw: list[dict[str, Any]] = message.provider_raw
        return raw
    content: list[dict[str, Any]] = []
    for b in message.blocks:
        if isinstance(b, TextBlock) and b.text:
            content.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolCallBlock):
            content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.arguments})
    if not content:
        # The API rejects empty content and empty text blocks.
        content.append({"type": "text", "text": EMPTY_TEXT_PLACEHOLDER})
    return content


def to_anthropic_request(req: ModelRequest, profile: ModelProfile) -> dict[str, Any]:
    """Build the keyword arguments for messages.create from a neutral request."""
    tools: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in req.tools
    ]
    if tools:
        # One breakpoint after the last tool caches system plus tools as a stable prefix.
        tools[-1]["cache_control"] = dict(_EPHEMERAL)
    messages = [
        {
            "role": m.role,
            "content": _assistant_content(m) if m.role == "assistant" else _user_content(m),
        }
        for m in req.messages
    ]
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "max_tokens": req.max_output_tokens,
        "system": [{"type": "text", "text": req.system, "cache_control": dict(_EPHEMERAL)}],
        "messages": messages,
        "thinking": {"type": "adaptive"},
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = {"type": "auto"}
    if profile.effort is not None:
        kwargs["output_config"] = {"effort": profile.effort}
    return kwargs


def from_anthropic_response(
    resp: AnthropicMessage, profile: ModelProfile, latency_ms: int
) -> ModelResponse:
    """Convert an SDK Message into a neutral ModelResponse."""
    blocks: list[TextBlock | ToolCallBlock] = []
    for block in resp.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            blocks.append(ToolCallBlock(id=block.id, name=block.name, arguments=dict(block.input)))
    if not blocks:
        # A turn with only thinking, or an empty refusal, still needs one neutral block.
        blocks.append(TextBlock(text=""))
    raw = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
    usage = Usage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_read_tokens=resp.usage.cache_read_input_tokens or 0,
        cache_write_tokens=resp.usage.cache_creation_input_tokens or 0,
    )
    return ModelResponse(
        message=Message(role="assistant", blocks=list(blocks), provider_raw=raw, provider=PROVIDER),
        stop_reason=_STOP_REASONS.get(resp.stop_reason or "", "other"),
        usage=usage,
        latency_ms=latency_ms,
        model=resp.model,
        provider=PROVIDER,
        request_id=resp._request_id,
    )


class AnthropicAdapter:
    def __init__(self, profile: ModelProfile, client: AsyncAnthropic | None = None) -> None:
        if profile.provider != PROVIDER:
            raise ValueError(f"profile {profile.name!r} is not an anthropic profile")
        self.profile = profile
        self._client = client

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            # The SDK reads ANTHROPIC_API_KEY itself; we never touch or log the value.
            self._client = AsyncAnthropic()
        return self._client

    async def complete(self, req: ModelRequest) -> ModelResponse:
        client = self._get_client()
        kwargs = to_anthropic_request(req, self.profile)
        start = time.perf_counter()
        resp = await client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if not isinstance(resp, AnthropicMessage):
            raise TypeError("expected a non-streaming Message from messages.create")
        return from_anthropic_response(resp, self.profile, latency_ms)
