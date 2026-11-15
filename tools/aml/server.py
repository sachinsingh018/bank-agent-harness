"""The AML investigation tools as an MCP server over stdio. Read-only.

Stdout is the protocol channel, so nothing here prints. Logs go to stderr, one line per call:
tool name, duration, ok or error code, response size and the truncated flag. Never arguments,
results, statements or connection details.

There is no server-side timeout: Neon poolers reject startup options. The client enforces the
10 second limit with read_timeout_seconds=TOOL_TIMEOUT_SECONDS (see tools/aml/caps.py).
"""

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from pydantic import BaseModel, ValidationError

from tools.aml.db import make_tool_engine, open_readonly
from tools.aml.descriptions import load_descriptions
from tools.aml.models import ErrorCode, ToolError
from tools.aml.paging import ToolFailure
from tools.aml.query_registry import QUERY_REGISTRY, QueryEntry

SERVER_NAME = "aml-investigation"
INTERNAL_MESSAGE = "internal error while running the tool (details hidden)"
NO_DATABASE_MESSAGE = "database is not configured (details hidden)"

log = logging.getLogger("aml_tools")


def _failure_for(exc: Exception) -> ToolFailure:
    """Turn any exception into a typed failure that is safe to show the agent."""
    if isinstance(exc, ToolFailure):
        return exc
    if isinstance(exc, ValidationError):
        # Field names and rule messages only. Input values and URLs are left out.
        parts = [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False, include_context=False)
        ]
        return ToolFailure("invalid_argument", "invalid arguments: " + "; ".join(parts))
    return ToolFailure("internal", INTERNAL_MESSAGE)


def _execute(entry: QueryEntry, engine: Any, arguments: dict[str, Any]) -> BaseModel:
    """Runs in a worker thread: validate, open a read-only connection, run the query."""
    inp = entry.input_model.model_validate(arguments)
    if not entry.needs_connection:
        return entry.call(None, inp)
    if engine is None:
        raise ToolFailure("internal", NO_DATABASE_MESSAGE)
    with open_readonly(engine) as conn:
        return entry.call(conn, inp)


def _log_call(name: str, started: float, status: str, size: int, truncated: bool) -> None:
    millis = int((time.perf_counter() - started) * 1000)
    log.info("tool=%s ms=%d status=%s size=%d truncated=%s", name, millis, status, size, truncated)


def _signature(entry: QueryEntry) -> inspect.Signature:
    """The tool's parameters, taken from its input model so the model stays the one source."""
    params = [
        inspect.Parameter(
            field_name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if field.is_required() else field.default,
            annotation=Annotated[field.annotation, field],
        )
        for field_name, field in entry.input_model.model_fields.items()
    ]
    return inspect.Signature(params, return_annotation=entry.output_model)


def _handler(entry: QueryEntry, engine: Any) -> Callable[..., Awaitable[BaseModel]]:
    async def handler(**arguments: Any) -> BaseModel:
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(_execute, entry, engine, arguments)
        except Exception as exc:
            failure = _failure_for(exc)
            _log_call(entry.name, started, failure.code, 0, False)
            code: ErrorCode = failure.code
            payload = ToolError(code=code, message=failure.message).model_dump_json()
            raise McpToolError(payload) from None
        size = len(result.model_dump_json())
        _log_call(entry.name, started, "ok", size, bool(getattr(result, "truncated", False)))
        return result

    handler.__name__ = entry.name
    handler.__qualname__ = entry.name
    handler.__signature__ = _signature(entry)  # type: ignore[attr-defined]
    return handler


def build_server(engine: Any = None) -> MCPServer:
    """The server with the nine tools. Without an engine it can list tools but not query."""
    descriptions = load_descriptions(entry.name for entry in QUERY_REGISTRY)
    server = MCPServer(SERVER_NAME, version=descriptions.version)
    for entry in QUERY_REGISTRY:
        server.tool(
            name=entry.name,
            description=descriptions.tools[entry.name],
            structured_output=True,
        )(_handler(entry, engine))
    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )  # the default stream is stderr
    try:
        engine = make_tool_engine()
    except Exception as exc:
        # The existing messages are generic by design and never hold the URL.
        raise SystemExit(f"aml tools server cannot start: {exc}") from None
    build_server(engine).run(transport="stdio")


if __name__ == "__main__":
    main()
