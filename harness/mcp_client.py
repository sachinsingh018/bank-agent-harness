"""Generic MCP client for the harness: start a tools server over stdio, list and call its tools.

It knows nothing about any module. Every call returns a ToolOutcome holding the exact JSON text
the model will see, so the loop never handles raw MCP results or exceptions.

Errors follow one convention shared by the tool servers: an error result whose text holds a JSON
object `{"code": ..., "message": ...}`. Arguments are not validated here; the server does that.
"""

from __future__ import annotations

import json
import re
import sys
import time
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Literal, Self, TextIO, get_args

import anyio
from mcp import Client, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pydantic import BaseModel, ConfigDict

from harness.models.types import ToolSpec

OutcomeCode = Literal["not_found", "invalid_argument", "too_large", "internal", "timeout"]
_SERVER_CODES: frozenset[str] = frozenset(get_args(OutcomeCode)) - {"timeout"}

INTERNAL_MESSAGE = "the tool call failed (details hidden)"
DEAD_MESSAGE = "the tool server is no longer running (details hidden)"
MAX_ERROR_MESSAGE_CHARS = 1000
# Starting the server, the handshake and the tool listing get their own, longer timeout.
STARTUP_TIMEOUT_SECONDS = 30.0
MAX_FIELD_CHARS = 200
# Shapes of the MCP SDK error text (mcp/server/mcpserver/tools/base.py and tool_manager.py).
_TOOL_NAME = r"[A-Za-z0-9_.\-]{1,128}"
_SDK_PREFIX = re.compile(rf"Error executing tool (?P<tool>{_TOOL_NAME}): ")
_UNKNOWN_TOOL = re.compile(rf"Unknown tool: {_TOOL_NAME}")
# Pydantic ValidationError text: a header, then per error a location line, an indented message
# line ending in [type=..., input_value=..., input_type=...] and an optional link line.
_ARG_HEADER = re.compile(r"(?P<count>[0-9]+) validation errors? for (?P<model>\S+)")
_ARG_LOC = re.compile(r"[A-Za-z0-9_.\[\]\-]{1,200}")
_ARG_DETAIL = re.compile(r"  (?P<msg>[^\n]+?) \[type=[a-z_]+(, [^\n]*)?\]")
_ARG_LINK = re.compile(r"    For further information visit https://errors\.pydantic\.dev/\S+")
_CLOSED_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


class ToolErrorPayload(BaseModel):
    """The error JSON the model sees."""

    model_config = ConfigDict(extra="forbid")

    code: OutcomeCode
    message: str


class ToolOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    content: str
    error_code: OutcomeCode | None
    duration_ms: int
    size: int
    truncated: bool
    session_dead: bool = False


def _dump(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _truncated(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("truncated"), bool):
        return bool(data["truncated"])
    transfers = data.get("transfers")
    if isinstance(transfers, dict) and isinstance(transfers.get("truncated"), bool):
        return bool(transfers["truncated"])
    return False


def _server_error(rest: str) -> ToolErrorPayload | None:
    """The tool server's own error: the whole remainder is one JSON object with a known code."""
    try:
        parsed = json.loads(rest)
    except ValueError:
        return None
    if (
        isinstance(parsed, dict)
        and set(parsed) == {"code", "message"}
        and parsed["code"] in _SERVER_CODES
        and isinstance(parsed["message"], str)
    ):
        return ToolErrorPayload(code=parsed["code"], message=parsed["message"])
    return None


def _argument_error(tool: str, rest: str) -> ToolErrorPayload | None:
    """The MCP SDK's argument check: `Error executing tool <name>: <pydantic ValidationError>`.

    The text is accepted only when it has exactly that shape. The message is rebuilt from field
    locations and rule messages; input values, types and documentation links are dropped.
    """
    lines = rest.split("\n")
    header = _ARG_HEADER.fullmatch(lines[0])
    if header is None or header.group("model") != f"{tool}Arguments":
        return None
    count = int(header.group("count"))
    parts: list[str] = []
    body = lines[1:]
    i = 0
    while i < len(body):
        loc = body[i]
        if not _ARG_LOC.fullmatch(loc) or i + 1 >= len(body):
            return None
        detail = _ARG_DETAIL.fullmatch(body[i + 1])
        if detail is None:
            return None
        i += 2
        if i < len(body) and _ARG_LINK.fullmatch(body[i]):
            i += 1
        parts.append(f"{loc[:MAX_FIELD_CHARS]}: {detail.group('msg')[:MAX_FIELD_CHARS]}")
    if not parts or len(parts) != count:
        return None
    message = ("invalid arguments: " + "; ".join(parts))[:MAX_ERROR_MESSAGE_CHARS]
    return ToolErrorPayload(code="invalid_argument", message=message)


def _error_payload(text: str) -> ToolErrorPayload:
    """Turn the text of an error result into a typed payload. Only two exact shapes pass
    through: the tool server's error JSON and the SDK's argument check. Anything else is
    hidden behind a generic message."""
    stripped = text.strip()
    if _UNKNOWN_TOOL.fullmatch(stripped):
        return ToolErrorPayload(code="invalid_argument", message="unknown tool")
    prefix = _SDK_PREFIX.match(stripped)
    if prefix is not None:
        rest = stripped[prefix.end() :]
        found = _server_error(rest) or _argument_error(prefix.group("tool"), rest)
        if found is not None:
            return found
    return ToolErrorPayload(code="internal", message=INTERNAL_MESSAGE)


class ToolSession:
    """`async with ToolSession(params, read_timeout_seconds=...) as s:` starts the server,
    initializes and lists tools. The per call timeout comes from the module spec."""

    def __init__(
        self,
        params: StdioServerParameters,
        *,
        read_timeout_seconds: float,
        startup_timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
        errlog: TextIO | None = None,
    ) -> None:
        self._params = params
        self._timeout = read_timeout_seconds
        self._startup_timeout = startup_timeout_seconds
        self._errlog = errlog if errlog is not None else sys.stderr
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self.tools: list[ToolSpec] = []
        self.dead = False

    @classmethod
    def open(
        cls, params: StdioServerParameters, *, read_timeout_seconds: float, **kwargs: Any
    ) -> ToolSession:
        return cls(params, read_timeout_seconds=read_timeout_seconds, **kwargs)

    async def __aenter__(self) -> Self:
        async with AsyncExitStack() as stack:
            transport = stdio_client(self._params, errlog=self._errlog)
            client = Client(transport, read_timeout_seconds=self._startup_timeout)
            await stack.enter_async_context(client)
            listed = (await client.list_tools()).tools
            self.tools = [
                ToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema),
                )
                for tool in listed
            ]
            self._client = client
            self._stack = stack.pop_all()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.__aexit__(exc_type, exc, tb)

    def _failure(self, code: OutcomeCode, message: str, started: float) -> ToolOutcome:
        content = ToolErrorPayload(code=code, message=message).model_dump_json()
        return ToolOutcome(
            ok=False,
            content=content,
            error_code=code,
            duration_ms=int((time.perf_counter() - started) * 1000),
            size=len(content),
            truncated=False,
            session_dead=self.dead,
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        if self._client is None or self.dead:
            self.dead = True
            return self._failure("internal", DEAD_MESSAGE, started)
        try:
            result = await self._client.call_tool(
                name, arguments, read_timeout_seconds=self._timeout
            )
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                return self._failure("timeout", self._timeout_message(), started)
            if exc.code == CONNECTION_CLOSED:
                self.dead = True
                return self._failure("internal", DEAD_MESSAGE, started)
            return self._failure("internal", INTERNAL_MESSAGE, started)
        except TimeoutError:
            return self._failure("timeout", self._timeout_message(), started)
        except _CLOSED_ERRORS:
            self.dead = True
            return self._failure("internal", DEAD_MESSAGE, started)
        except Exception:
            return self._failure("internal", INTERNAL_MESSAGE, started)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            payload = _error_payload(text)
            return self._failure(payload.code, payload.message, started)
        data = result.structured_content
        content = _dump(data) if data is not None else text
        return ToolOutcome(
            ok=True,
            content=content,
            error_code=None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            size=len(content),
            truncated=_truncated(data),
        )

    def _timeout_message(self) -> str:
        return f"the tool did not answer within {self._timeout:g} seconds"
