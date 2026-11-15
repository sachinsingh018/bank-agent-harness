"""Neutral request, response, block and usage types shared by every model adapter."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.models.config import ModelProfile

# Sent in place of an assistant turn that has no text and no tool calls, because
# providers reject empty assistant content.
EMPTY_TEXT_PLACEHOLDER = "(no text)"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextBlock(_Strict):
    type: Literal["text"] = "text"
    text: str


class ToolCallBlock(_Strict):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the provider sent arguments that could not be parsed as a JSON object.
    # arguments is then empty, but the call keeps its id so an error result can answer it.
    parse_error: str | None = None


class ToolResultBlock(_Strict):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: str
    is_error: bool = False


Block = Annotated[TextBlock | ToolCallBlock | ToolResultBlock, Field(discriminator="type")]


class Message(_Strict):
    role: Literal["user", "assistant"]
    blocks: list[Block]
    # Raw provider content for assistant turns, replayed unchanged to the same provider
    # (for example Claude thinking blocks). Must be JSON-compatible data.
    provider_raw: Any | None = None
    # Provider that produced provider_raw. Required when provider_raw is set.
    provider: str | None = None

    @model_validator(mode="after")
    def _check_blocks(self) -> Self:
        if not self.blocks:
            raise ValueError("a message needs at least one block")
        for block in self.blocks:
            if isinstance(block, ToolCallBlock) and self.role != "assistant":
                raise ValueError("tool_call blocks are only allowed in assistant messages")
            if isinstance(block, ToolResultBlock) and self.role != "user":
                raise ValueError("tool_result blocks are only allowed in user messages")
        if self.provider_raw is not None and not self.provider:
            raise ValueError("provider is required when provider_raw is set")
        return self

    def tool_calls(self) -> list[ToolCallBlock]:
        return [b for b in self.blocks if isinstance(b, ToolCallBlock)]

    def text(self) -> str:
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))


class ToolSpec(_Strict):
    name: str
    description: str
    input_schema: dict[str, Any]


class Usage(_Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        """Every processed token: input + cache_read + cache_write + output.

        Cached tokens count too, because each turn resends the whole growing
        conversation. This is the number checked against max_total_tokens.
        """
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ModelRequest(_Strict):
    system: str
    messages: list[Message]
    tools: list[ToolSpec]
    max_output_tokens: int = Field(gt=0)


class ModelResponse(_Strict):
    message: Message
    stop_reason: StopReason
    usage: Usage
    latency_ms: int = Field(ge=0)
    model: str
    provider: str
    request_id: str | None = None


class ModelAdapter(Protocol):
    profile: ModelProfile

    async def complete(self, req: ModelRequest) -> ModelResponse: ...
