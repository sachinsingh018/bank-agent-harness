"""The stored form of a run's steps, for the replay pages (docs/plans/investigation-replay.md).

Pure code, no database. harness.records writes a StoredTrace to the append-only run_traces
table next to each agent run: the maker's RunTrace and, when the checker ran, the checker's.
Tool result text is capped at MAX_TOOL_RESULT_CHARS per result. JSON results are capped by
leaving out list items from the end of the longest lists, so they stay valid JSON and the
replay can still read them; a "_capped" note says how many items were left out. Text that is
not JSON is cut and ends with CUT_MARK.

The case card is the module's case tool result as the agent first read it (the alert for AML),
parsed from the uncapped trace and then capped the same way. The gallery reads it, so it never
has to load whole traces.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.models.types import Message, TextBlock, ToolCallBlock, ToolResultBlock
from harness.trace import CaseResult, RunTrace, ToolResultContent

MAX_TOOL_RESULT_CHARS = 12_000
CUT_MARK = " [cut to keep the record small]"
CAPPED_KEY = "_capped"
# The tool whose result describes the case, per module. Other modules have no card yet.
CASE_TOOLS: dict[str, str] = {"aml": "get_alert"}
_MAX_TRIM_ROUNDS = 200


class StoredTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal[1] = 1
    tool_result_cap: int
    maker: RunTrace
    checker: RunTrace | None = None  # None when the checker did not run


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _lists(data: Any) -> list[list[Any]]:
    """Every non-empty list inside data (lists in lists too), in document order."""
    found: list[list[Any]] = []
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            if item:
                found.append(item)
            stack.extend(reversed(item))
    return found


def _trim_json(data: dict[str, Any], limit: int) -> str | None:
    """Leave out list items from the end of the largest list until the text fits, or None."""
    dropped = 0
    for _ in range(_MAX_TRIM_ROUNDS):
        note = {CAPPED_KEY: f"{dropped} list items left out"} if dropped else {}
        text = _dumps({**data, **note})
        if len(text) <= limit:
            return text
        lists = _lists(data)
        if not lists:
            return None
        largest = max(lists, key=lambda x: len(_dumps(x)))
        excess = len(text) - limit
        # Drop enough items from the end to cover the excess (at least one, at most all).
        while largest and excess > 0:
            excess -= len(_dumps(largest.pop())) + 1
            dropped += 1
    return None


def cap_content(content: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """content when it fits; else valid JSON with list items left out, or cut text."""
    if len(content) <= limit:
        return content
    try:
        data = json.loads(content)
    except ValueError:
        data = None
    if isinstance(data, dict):
        trimmed = _trim_json(data, limit)
        if trimmed is not None:
            return trimmed
    return content[: max(0, limit - len(CUT_MARK))] + CUT_MARK


def _cap_message(message: Message, limit: int) -> Message:
    if not any(isinstance(b, ToolResultBlock) for b in message.blocks):
        return message
    blocks: list[TextBlock | ToolCallBlock | ToolResultBlock] = [
        b.model_copy(update={"content": cap_content(b.content, limit)})
        if isinstance(b, ToolResultBlock)
        else b
        for b in message.blocks
    ]
    return message.model_copy(update={"blocks": blocks})


def cap_trace(trace: RunTrace, limit: int = MAX_TOOL_RESULT_CHARS) -> RunTrace:
    """A copy with every tool result text capped. Provider raw content is dropped: the replay
    never needs it and it can be large."""
    messages = [
        _cap_message(m, limit).model_copy(update={"provider_raw": None, "provider": None})
        for m in trace.messages
    ]
    results = [
        ToolResultContent(index=r.index, content=cap_content(r.content, limit))
        for r in trace.tool_results
    ]
    return trace.model_copy(update={"messages": messages, "tool_results": results})


def first_tool_result(trace: RunTrace, tool: str) -> str | None:
    """The text of the first successful result of the named tool, or None."""
    wanted: set[str] = set()
    for message in trace.messages:
        for block in message.blocks:
            if isinstance(block, ToolCallBlock) and block.name == tool:
                wanted.add(block.id)
            elif (
                isinstance(block, ToolResultBlock)
                and block.tool_call_id in wanted
                and not block.is_error
            ):
                return block.content
    return None


def case_card(trace: RunTrace, limit: int = MAX_TOOL_RESULT_CHARS) -> dict[str, Any] | None:
    """The module's case tool result as a JSON object (capped), or None."""
    tool = CASE_TOOLS.get(trace.module)
    if tool is None:
        return None
    content = first_tool_result(trace, tool)
    if content is None:
        return None
    try:
        data = json.loads(cap_content(content, limit))
    except ValueError:
        return None
    if not isinstance(data, dict) or ("code" in data and "message" in data):
        return None  # not an object, or a tool error
    return data


def stored_trace(
    result: CaseResult, limit: int = MAX_TOOL_RESULT_CHARS
) -> tuple[StoredTrace, dict[str, Any] | None]:
    """The trace to store for a case result, and its case card."""
    checker = None if result.review is None else cap_trace(result.review.trace, limit)
    stored = StoredTrace(
        tool_result_cap=limit, maker=cap_trace(result.trace, limit), checker=checker
    )
    return stored, case_card(result.trace, limit)
