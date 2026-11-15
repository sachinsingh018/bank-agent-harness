"""No-network adapter that replays a scripted list of responses (tests and CI fast eval)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from harness.models.config import ModelProfile, Prices
from harness.models.types import (
    Message,
    ModelRequest,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolCallBlock,
    Usage,
)

ScriptItem = ModelResponse | Callable[[ModelRequest], ModelResponse]

_ZERO = Prices(input=Decimal(0), output=Decimal(0), cache_read=Decimal(0), cache_write=Decimal(0))


class ScriptExhaustedError(RuntimeError):
    """The adapter was called more times than the script has responses."""


def default_scripted_profile() -> ModelProfile:
    return ModelProfile(
        name="scripted",
        provider="scripted",
        model="scripted",
        max_output_tokens=16_000,
        prices=_ZERO,
    )


class ScriptedAdapter:
    def __init__(
        self, responses: Sequence[ScriptItem], profile: ModelProfile | None = None
    ) -> None:
        self.profile: ModelProfile = profile or default_scripted_profile()
        self._script: list[ScriptItem] = list(responses)
        self._next = 0
        self.requests: list[ModelRequest] = []

    @property
    def remaining(self) -> int:
        return len(self._script) - self._next

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req.model_copy(deep=True))
        if self._next >= len(self._script):
            raise ScriptExhaustedError(
                f"scripted adapter ran out of responses after {len(self._script)} call(s)"
            )
        item = self._script[self._next]
        self._next += 1
        return item if isinstance(item, ModelResponse) else item(req)


def _response(message: Message, stop_reason: StopReason, usage: Usage | None) -> ModelResponse:
    return ModelResponse(
        message=message,
        stop_reason=stop_reason,
        usage=usage or Usage(input_tokens=100, output_tokens=20),
        latency_ms=0,
        model="scripted",
        provider="scripted",
        request_id=None,
    )


def text_response(
    text: str, stop_reason: StopReason = "end_turn", usage: Usage | None = None
) -> ModelResponse:
    """An assistant turn with only text."""
    message = Message(role="assistant", blocks=[TextBlock(text=text)])
    return _response(message, stop_reason, usage)


def tool_call_response(
    calls: Sequence[tuple[str, dict[str, Any]]],
    text: str | None = None,
    usage: Usage | None = None,
    id_prefix: str = "call",
) -> ModelResponse:
    """An assistant turn asking for one or more tool calls, given as (name, arguments)."""
    blocks: list[TextBlock | ToolCallBlock] = [TextBlock(text=text)] if text else []
    blocks += [
        ToolCallBlock(id=f"{id_prefix}_{i}", name=name, arguments=args)
        for i, (name, args) in enumerate(calls)
    ]
    message = Message(role="assistant", blocks=list(blocks))
    return _response(message, "tool_use", usage)
