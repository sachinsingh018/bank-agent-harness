"""Observability: send each case run to Langfuse as one trace (brief section 4).

The harness talks only to the small Tracer and RunTracer protocols below. NoopTracer is the
default everywhere, so the harness works without Langfuse. LangfuseTracer is built only when
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST are all set (tracer_from_env).

Tracing never changes a case outcome: the loop, the checker and the runner only see tracers
wrapped by safe_tracer or safe_run, which swallow every exception after printing the exception
class name (never the message) to stderr.

One case is one trace:
    case root span (module-case): metadata (module, prompt version and sha, profile, dataset,
        eval run id), output at the end (status, reason, totals, cost, lesson ids)
      generation per model call: model, usage, cost, provider, latency, stop reason, request id
      span per tool call: name, arguments, ok or error code, size, duration, truncated, repeat,
          arg repairs, the result text
      span "verifiers": every verifier result
      span "review" (the checker, when it runs): its own generations, tool spans, verifiers

Data sent: the case data is synthetic, so tool result text and model messages are sent, each
string capped at MAX_FIELD_CHARS. Before anything leaves the process every string is cleaned:
values of the secret env vars (SCRUBBED_ENV) are replaced and URLs are removed. Keys are read
from the env and passed to the client only; they are never printed, logged or put in a trace.
Spans are created after the work they describe is done (the harness records results, not
live calls), so their own durations are near zero; the real latency and duration are in the
metadata.

Langfuse SDK calls used, confirmed against the installed langfuse 5.1.0 source
(langfuse/_client/client.py, span.py, propagation.py):
    Langfuse(public_key=..., secret_key=..., base_url=...)   base_url wins over the env
    propagate_attributes(trace_name=..., session_id=..., tags=..., metadata=...)
        context manager; sets trace level attributes on spans started inside it
        (metadata values must encode to 200 characters or fewer)
    client.start_observation(name=..., as_type="span", input=..., metadata=...)
        a new root observation (not made current); .trace_id and .id are hex strings
    observation.start_observation(name=..., as_type="generation" | "span", input=...,
        output=..., metadata=..., level=..., status_message=..., model=...,
        usage_details={str: int}, cost_details={str: float})   a child observation
    observation.update(output=..., metadata=..., level=..., status_message=...)
    observation.end()
    client.flush()   blocks until queued spans are delivered
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, cast

from harness.models.types import (
    Message,
    ModelRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
)
from harness.trace import ModelCallRecord, ToolCallRecord
from harness.verifiers.base import VerifierResult

MAX_FIELD_CHARS = 8_000
PUBLIC_KEY_ENV = "LANGFUSE_PUBLIC_KEY"
SECRET_KEY_ENV = "LANGFUSE_SECRET_KEY"
HOST_ENV = "LANGFUSE_HOST"
TRACING_ENV = (PUBLIC_KEY_ENV, SECRET_KEY_ENV, HOST_ENV)
# Values of these env vars are replaced wherever they appear in traced data.
SCRUBBED_ENV = (
    *TRACING_ENV,
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DATABASE_URL",
    "HARNESS_WRITE_DATABASE_URL",
    "TEST_DATABASE_URL",
)
REDACTED = "[redacted]"
URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")
MIN_SECRET_CHARS = 6  # shorter env values are not scrubbed (they would match everywhere)


def _warn(exc: Exception) -> None:
    print(f"warning: tracing failed ({type(exc).__name__})", file=sys.stderr)


# ---- protocols and data -------------------------------------------------------------------


@dataclass(frozen=True)
class TraceMeta:
    """What the case trace is labelled with."""

    case_id: str
    module: str
    profile: str
    prompt_version: str
    prompt_sha256: str
    dataset: str | None = None
    eval_run_id: str | None = None


@dataclass(frozen=True)
class RunEnd:
    """How one agent run (the case, or the checker inside it) ended."""

    status: str
    reason: str | None
    totals: Usage
    cost_usd: str
    tool_steps: int
    wall_ms: int
    lesson_ids: tuple[int, ...] = ()
    error_class: str | None = None


class RunTracer(Protocol):
    """Records one agent run: the case root, or the checker's review span."""

    @property
    def trace_id(self) -> str | None: ...

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        """One model call, as a generation."""

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        """One tool call (investigation or submit), as a span."""

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        """The verifier results for one submission."""

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        """A child span for the checker; its model and tool calls go under it."""

    def end(self, end: RunEnd) -> None:
        """Close this run with its outcome."""


class Tracer(Protocol):
    @property
    def host(self) -> str | None:
        """The Langfuse host, or None when nothing is sent."""

    def start_case(self, meta: TraceMeta) -> RunTracer: ...

    def flush(self) -> None:
        """Send everything queued. Called at the end of a run."""


# ---- no-op ---------------------------------------------------------------------------------


class NoopRunTracer:
    @property
    def trace_id(self) -> str | None:
        return None

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        return None

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        return None

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        return None

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        return self

    def end(self, end: RunEnd) -> None:
        return None


class NoopTracer:
    @property
    def host(self) -> str | None:
        return None

    def start_case(self, meta: TraceMeta) -> RunTracer:
        return NOOP_RUN

    def flush(self) -> None:
        return None


NOOP_RUN = NoopRunTracer()
NOOP_TRACER = NoopTracer()


# ---- safety wrappers -----------------------------------------------------------------------


class _SafeRun:
    def __init__(self, inner: RunTracer) -> None:
        self._inner = inner

    @property
    def trace_id(self) -> str | None:
        try:
            value = self._inner.trace_id
        except Exception as exc:
            _warn(exc)
            return None
        return value if isinstance(value, str) else None

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        try:
            self._inner.model_call(record, request, output)
        except Exception as exc:
            _warn(exc)

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        try:
            self._inner.tool_call(record, content)
        except Exception as exc:
            _warn(exc)

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        try:
            self._inner.verifiers(results)
        except Exception as exc:
            _warn(exc)

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        try:
            return safe_run(self._inner.start_review(profile, prompt_version, prompt_sha256))
        except Exception as exc:
            _warn(exc)
            return NOOP_RUN

    def end(self, end: RunEnd) -> None:
        try:
            self._inner.end(end)
        except Exception as exc:
            _warn(exc)


class _SafeTracer:
    def __init__(self, inner: Tracer) -> None:
        self._inner = inner

    @property
    def host(self) -> str | None:
        try:
            return self._inner.host
        except Exception as exc:
            _warn(exc)
            return None

    def start_case(self, meta: TraceMeta) -> RunTracer:
        try:
            return safe_run(self._inner.start_case(meta))
        except Exception as exc:
            _warn(exc)
            return NOOP_RUN

    def flush(self) -> None:
        try:
            self._inner.flush()
        except Exception as exc:
            _warn(exc)


def safe_run(run: RunTracer) -> RunTracer:
    """run, with every call made unable to raise."""
    return run if isinstance(run, _SafeRun | NoopRunTracer) else _SafeRun(run)


def safe_tracer(tracer: Tracer) -> Tracer:
    return tracer if isinstance(tracer, _SafeTracer | NoopTracer) else _SafeTracer(tracer)


# ---- sampling ------------------------------------------------------------------------------


def sampled(case_id: str, run_id: str, rate: float) -> bool:
    """Deterministic sampling for routine runs: the same case and run id always give the same
    answer. rate 0 never samples, rate 1 always does."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"{case_id}:{run_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


# ---- cleaning ------------------------------------------------------------------------------


class Cleaner:
    """Caps strings and removes secret env values and URLs from data before it is sent."""

    def __init__(self, env: Mapping[str, str], max_chars: int = MAX_FIELD_CHARS) -> None:
        values = {env.get(name, "") for name in SCRUBBED_ENV}
        self._secrets = sorted((v for v in values if len(v) >= MIN_SECRET_CHARS), key=len)[::-1]
        self._max = max_chars

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, REDACTED)
        value = URL_PATTERN.sub("[url]", value)
        if len(value) > self._max:
            value = value[: self._max] + f"... [{len(value) - self._max} more characters]"
        return value

    def data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {self.text(str(k)): self.data(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.data(v) for v in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        return self.text(str(value))


def _block(block: TextBlock | ToolCallBlock | ToolResultBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolCallBlock):
        args = json.dumps(block.arguments, ensure_ascii=False, sort_keys=True)
        return {"type": "tool_call", "id": block.id, "name": block.name, "arguments": args}
    return {
        "type": "tool_result",
        "tool_call_id": block.tool_call_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def message_data(message: Message) -> dict[str, Any]:
    """A message as plain data, without provider_raw."""
    return {"role": message.role, "blocks": [_block(b) for b in message.blocks]}


def _usage(usage: Usage) -> dict[str, int]:
    return {
        "input": usage.input_tokens,
        "output": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read_tokens,
        "cache_creation_input_tokens": usage.cache_write_tokens,
    }


# ---- Langfuse ------------------------------------------------------------------------------


class ObservationLike(Protocol):
    """The part of a Langfuse observation the tracer uses."""

    trace_id: str
    id: str

    def start_observation(self, **kwargs: Any) -> Any: ...

    def update(self, **kwargs: Any) -> Any: ...

    def end(self) -> Any: ...


class ClientLike(Protocol):
    """The part of the Langfuse client the tracer uses."""

    def start_observation(self, **kwargs: Any) -> Any: ...

    def flush(self) -> None: ...


class LangfuseRunTracer:
    def __init__(self, obs: ObservationLike, clean: Cleaner) -> None:
        self._obs = obs
        self._clean = clean
        self._sent = 0  # messages already shown in an earlier generation

    @property
    def trace_id(self) -> str | None:
        return self._obs.trace_id

    def _child(self, **kwargs: Any) -> None:
        for key in ("input", "output", "metadata"):
            if key in kwargs:
                kwargs[key] = self._clean.data(kwargs[key])
        self._obs.start_observation(**kwargs).end()

    def model_call(self, record: ModelCallRecord, request: ModelRequest, output: Message) -> None:
        # Each generation shows only the messages added since the previous one, so the whole
        # conversation is in the trace once, not once per call.
        new = request.messages[self._sent :]
        self._sent = len(request.messages) + 1  # plus the reply, which the next call resends
        model_input: dict[str, Any] = {"messages": [message_data(m) for m in new]}
        if record.index == 0:
            model_input["system"] = request.system
        self._child(
            name=f"model-call-{record.index}",
            as_type="generation",
            model=self._clean.text(record.model),
            input=model_input,
            output=message_data(output),
            usage_details=_usage(record.usage),
            cost_details={"total": float(Decimal(record.cost_usd))},
            metadata={
                "provider": record.provider,
                "latency_ms": record.latency_ms,
                "stop_reason": record.stop_reason,
                "request_id": record.request_id,
                "prompt_version": record.prompt_version,
                "prompt_sha256": record.prompt_sha256,
            },
        )

    def tool_call(self, record: ToolCallRecord, content: str) -> None:
        self._child(
            name=f"tool-{record.name}",
            as_type="span",
            input=record.arguments,
            output=content,
            level="DEFAULT" if record.ok else "ERROR",
            status_message=record.error_code,
            metadata={
                "index": record.index,
                "step": record.step,
                "ok": record.ok,
                "error_code": record.error_code,
                "size": record.size,
                "duration_ms": record.duration_ms,
                "truncated": record.truncated,
                "repeat": record.repeat,
                "arg_repairs": list(record.arg_repairs),
            },
        )

    def verifiers(self, results: Sequence[VerifierResult]) -> None:
        passed = all(r.passed for r in results)
        self._child(
            name="verifiers",
            as_type="span",
            output=[r.model_dump(mode="json") for r in results],
            level="DEFAULT" if passed else "WARNING",
            metadata={"passed": passed},
        )

    def start_review(self, profile: str, prompt_version: str, prompt_sha256: str) -> RunTracer:
        metadata = {
            "profile": profile,
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
        }
        child = self._obs.start_observation(
            name="review", as_type="span", metadata=self._clean.data(metadata)
        )
        return LangfuseRunTracer(child, self._clean)

    def end(self, end: RunEnd) -> None:
        output = {
            "status": end.status,
            "reason": end.reason,
            "totals": end.totals.model_dump(),
            "cost_usd": end.cost_usd,
            "tool_steps": end.tool_steps,
            "wall_ms": end.wall_ms,
            "lesson_ids": list(end.lesson_ids),
            "error_class": end.error_class,
        }
        self._obs.update(
            output=self._clean.data(output),
            level="DEFAULT" if end.status in ("proposed", "approve") else "WARNING",
            status_message=end.reason,
        )
        self._obs.end()


class LangfuseTracer:
    def __init__(self, client: ClientLike, host: str, clean: Cleaner) -> None:
        self._client = client
        self._host = host
        self._clean = clean

    @property
    def host(self) -> str | None:
        return self._host

    def start_case(self, meta: TraceMeta) -> RunTracer:
        from langfuse import propagate_attributes

        metadata = {
            "case_id": meta.case_id,
            "module": meta.module,
            "profile": meta.profile,
            "prompt_version": meta.prompt_version,
            "prompt_sha256": meta.prompt_sha256,
            "dataset": meta.dataset,
            "eval_run_id": meta.eval_run_id,
        }
        trace_meta = {k: self._clean.text(v) for k, v in metadata.items() if v is not None}
        tags = [meta.module, "eval" if meta.eval_run_id else "routine"]
        with propagate_attributes(
            trace_name=f"{meta.module}-case",
            session_id=meta.eval_run_id,
            tags=tags,
            metadata=trace_meta,
        ):
            root = self._client.start_observation(
                name=f"{meta.module}-case",
                as_type="span",
                input={"case_id": self._clean.text(meta.case_id)},
                metadata=trace_meta,
            )
        return LangfuseRunTracer(root, self._clean)

    def flush(self) -> None:
        self._client.flush()


def _langfuse_client(public_key: str, secret_key: str, host: str) -> ClientLike:
    """The real client. Tests replace this function so nothing is sent."""
    from langfuse import Langfuse

    client = Langfuse(public_key=public_key, secret_key=secret_key, base_url=host)
    # The SDK's overloaded start_observation does not match the **kwargs protocol for mypy.
    return cast(ClientLike, client)


def tracer_from_env(
    enabled: bool,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[str, str, str], ClientLike] | None = None,
) -> Tracer:
    """A LangfuseTracer when enabled and all three LANGFUSE env vars are set, else NoopTracer.
    A client that fails to start gives NoopTracer too (only the exception class is shown)."""
    env = os.environ if env is None else env
    if not enabled:
        return NOOP_TRACER
    public, secret, host = (env.get(name, "") for name in TRACING_ENV)
    if not (public and secret and host):
        return NOOP_TRACER
    factory = client_factory if client_factory is not None else _langfuse_client
    try:
        client = factory(public, secret, host)
    except Exception as exc:
        _warn(exc)
        return NOOP_TRACER
    return LangfuseTracer(client, host.rstrip("/"), Cleaner(env))
