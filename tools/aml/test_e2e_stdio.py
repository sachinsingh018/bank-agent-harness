"""End to end over real stdio: the server runs as a child process, as the harness will start it.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Alerts live under their own ruleset version and are removed again."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import pytest
from mcp import Client
from mcp.client.stdio import stdio_client
from sqlalchemy import Engine, select
from sqlalchemy.engine import make_url

from harness.db.models import Alert, AlertTransaction, Transaction
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.store import delete_alerts_for_version, write_alerts
from tests_support.aml_db import local_url, pick_samples
from tools.aml.caps import (
    MAX_COUNTERPARTIES,
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    MAX_PATTERN_MATCHES,
    MAX_RESPONSE_CHARS,
    MAX_ROWS,
    TOOL_TIMEOUT_SECONDS,
)
from tools.aml.conftest import ToolData
from tools.aml.launch import REPO_ROOT, server_params
from tools.aml.models import (
    AccountRef,
    GraphInput,
    PatternCheckInput,
    PatternName,
    ToolError,
)
from tools.aml.query_registry import QUERY_REGISTRY, Samples
from tools.aml.schema_snapshot import SNAPSHOT_PATH, render, snapshot_from_tools

E2E_VERSION = "test-tools-e2e"
# The name is built from two pieces so the full literal never appears in code under scan.
LABELS = "transaction" + "_labels"
FORBIDDEN_KEYS = {"is_laundering", "laundering", "attempt_id", "attempt_type"}
# Calls that may legitimately be refused as too large on some samples. Each of them must still
# succeed at least once, on the small sample.
MAY_BE_TOO_LARGE = {"run_pattern_check", "get_transaction_graph"}
SERVER_MARK = "tools.aml.server"


@dataclass
class Call:
    name: str
    args: dict[str, Any]
    is_error: bool
    structured: dict[str, Any] | None
    text: str


@dataclass
class Session:
    tool_names: list[str]
    snapshot: dict[str, Any]
    calls: list[Call]
    errors: dict[str, Call]
    stderr: str
    orphans: set[int]


def pick_small_samples(engine: Engine, version: str) -> Samples:
    """Samples from the smallest alert with an anchor, so its neighbourhood stays under the caps."""
    with engine.connect() as conn:
        alert = conn.execute(
            select(
                Alert.alert_id,
                Alert.anchor_bank,
                Alert.anchor_acct,
                Alert.window_start,
                Alert.window_end,
            )
            .where(Alert.ruleset_version == version, Alert.anchor_bank.is_not(None))
            .order_by(Alert.txn_count, Alert.alert_id)
            .limit(1)
        ).one()
        txn_id = conn.execute(
            select(Transaction.txn_id)
            .join(AlertTransaction, AlertTransaction.txn_id == Transaction.txn_id)
            .where(AlertTransaction.alert_id == alert.alert_id)
            .order_by(Transaction.ts, Transaction.txn_id)
            .limit(1)
        ).scalar_one()
    return Samples(
        alert_id=alert.alert_id,
        account=AccountRef(bank=alert.anchor_bank, acct=alert.anchor_acct),
        txn_id=txn_id,
        start=alert.window_start,
        end=alert.window_end,
    )


def _server_pids() -> set[int]:
    """Process ids whose command line runs the tools server (Linux /proc)."""
    pids: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if SERVER_MARK in cmdline and "pytest" not in cmdline:
            pids.add(int(entry.name))
    return pids


def _text(result: Any) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def _call(client: Client, name: str, args: dict[str, Any]) -> Call:
    result = await client.call_tool(name, args)
    return Call(name, args, result.is_error, result.structured_content, _text(result))


async def _drive(url: str, samples: Samples, small: Samples) -> Session:
    before = _server_pids()
    calls: list[Call] = []
    errors: dict[str, Call] = {}
    a = samples.account
    s = small.account
    during: set[int] = set()
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
        transport = stdio_client(server_params(url), errlog=errlog)
        async with Client(transport, read_timeout_seconds=TOOL_TIMEOUT_SECONDS) as client:
            listed = (await client.list_tools()).tools
            # Positive control: the same /proc matching must see the child while it runs.
            during = _server_pids() - before
            assert during, "the /proc matching found no server process while the client was open"
            for entry in QUERY_REGISTRY:
                for args in entry.build_args(samples):
                    payload = args.model_dump(mode="json")
                    calls.append(await _call(client, entry.name, payload))
            # Narrow window on a small alert: these must succeed, not hit the size cap.
            graph = GraphInput(bank=s.bank, acct=s.acct, start=small.start, end=small.end)
            calls.append(
                await _call(client, "get_transaction_graph", graph.model_dump(mode="json"))
            )
            for pattern in get_args(PatternName):
                check = PatternCheckInput(
                    pattern=pattern, bank=s.bank, acct=s.acct, start=small.start, end=small.end
                )
                calls.append(
                    await _call(client, "run_pattern_check", check.model_dump(mode="json"))
                )
            errors["unknown_alert"] = await _call(
                client, "get_alert", {"alert_id": "AL-000000000000"}
            )
            errors["unknown_account"] = await _call(
                client, "get_account", {"bank": 987654, "acct": "NOSUCHACCT"}
            )
            errors["bad_account"] = await _call(
                client, "get_account", {"bank": a.bank, "acct": "bad acct!"}
            )
            errors["hops"] = await _call(
                client, "get_transaction_graph", {"bank": a.bank, "acct": a.acct, "hops": 3}
            )
            errors["bad_detector"] = await _call(client, "describe_detector", {"detector": "x"})
        errlog.seek(0)
        stderr = errlog.read()
    after = _server_pids()
    return Session(
        tool_names=[t.name for t in listed],
        snapshot=snapshot_from_tools(list(listed), "v1"),
        calls=calls,
        errors=errors,
        stderr=stderr,
        orphans=after - before,
    )


@pytest.fixture(scope="module")
def e2e_data(local_engine: Engine) -> Iterator[ToolData]:
    delete_alerts_for_version(local_engine, E2E_VERSION)
    try:
        drafts = run_all(load_txns(local_engine))
        assert drafts
        write_alerts(local_engine, drafts, E2E_VERSION)
        yield ToolData(samples=pick_samples(local_engine, E2E_VERSION), drafts=drafts)
    finally:
        delete_alerts_for_version(local_engine, E2E_VERSION)


@pytest.fixture(scope="module")
def session(e2e_data: ToolData, local_engine: Engine) -> Session:
    small = pick_small_samples(local_engine, E2E_VERSION)
    return asyncio.run(_drive(local_url(), e2e_data.samples, small))


def _keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(str(k))
            found |= _keys(v)
    elif isinstance(value, list):
        for v in value:
            found |= _keys(v)
    return found


def _error(call: Call) -> ToolError:
    assert call.is_error
    start = call.text.index("{")
    return ToolError.model_validate(json.loads(call.text[start:]))


def test_lists_exactly_the_nine_tools(session: Session) -> None:
    assert sorted(session.tool_names) == sorted(e.name for e in QUERY_REGISTRY)
    assert len(session.tool_names) == 9


def test_listing_over_stdio_matches_the_snapshot(session: Session) -> None:
    assert render(session.snapshot) == SNAPSHOT_PATH.read_text(encoding="utf-8"), (
        "tool changes must be deliberate: regenerate with "
        "`uv run python -m tools.aml.schema_snapshot --write` and review the diff"
    )


def test_every_tool_runs_and_returns_its_output_model(session: Session) -> None:
    models = {e.name: e.output_model for e in QUERY_REGISTRY}
    assert len(session.calls) >= len(QUERY_REGISTRY)
    seen = set()
    for call in session.calls:
        if call.is_error:
            assert call.name in MAY_BE_TOO_LARGE, call.text
            assert _error(call).code == "too_large", call.text
            continue
        seen.add(call.name)
        assert call.structured is not None
        models[call.name].model_validate(call.structured)
    # Every tool, including the two that may be too large on big samples, succeeded at least once.
    assert seen == {e.name for e in QUERY_REGISTRY}


def test_lists_are_within_the_caps_and_say_if_cut(session: Session) -> None:
    for call in session.calls:
        data = call.structured
        if call.is_error or data is None:
            continue
        assert len(json.dumps(data, separators=(",", ":"))) <= MAX_RESPONSE_CHARS, call.name
        if "items" in data:
            limit = MAX_COUNTERPARTIES if call.name == "get_counterparties" else MAX_ROWS
            assert len(data["items"]) <= limit, call.name
            assert data["returned"] == len(data["items"]) or call.name == "get_counterparties"
            assert isinstance(data["total_count"], int) and isinstance(data["truncated"], bool)
            assert data["total_count"] >= len(data["items"])
        if call.name == "get_transaction_graph":
            assert len(data["nodes"]) <= MAX_GRAPH_NODES
            assert len(data["edges"]) <= MAX_GRAPH_EDGES
            assert isinstance(data["truncated"], bool)
        if call.name == "run_pattern_check":
            assert len(data["matches"]) <= MAX_PATTERN_MATCHES
            assert isinstance(data["truncated"], bool)


def test_typed_errors(session: Session) -> None:
    assert _error(session.errors["unknown_alert"]).code == "not_found"
    assert _error(session.errors["unknown_account"]).code == "not_found"
    assert _error(session.errors["bad_detector"]).code == "invalid_argument"
    # Argument validation by the SDK happens before our code runs, so the agent gets the
    # validation text as an error result naming the bad field.
    bad = session.errors["bad_account"]
    assert bad.is_error and "acct" in bad.text
    hops = session.errors["hops"]
    assert hops.is_error and "hops" in hops.text


def test_no_answer_key_words_in_any_output(session: Session) -> None:
    for call in [*session.calls, *session.errors.values()]:
        assert not (_keys(call.structured) & FORBIDDEN_KEYS), call.name
        for word in (*FORBIDDEN_KEYS, LABELS):
            assert word not in call.text, f"{call.name} output has {word}"


def test_stderr_has_no_connection_details_or_arguments(session: Session) -> None:
    url = os.environ["TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert session.stderr, "the server should log one line per call to stderr"
    assert parsed.password and parsed.username and parsed.host
    secrets = (
        url,
        f"{parsed.username}:{parsed.password}",
        f"@{parsed.host}",
        parsed.password,
        parsed.username,
        parsed.host,
    )
    for secret in secrets:
        assert secret not in session.stderr
    assert "AL-000000000000" not in session.stderr
    lines = [ln for ln in session.stderr.splitlines() if " aml_tools tool=" in ln]
    assert lines
    for line in lines:
        assert "status=" in line and "size=" in line and "truncated=" in line and "ms=" in line


def test_server_process_exits_with_the_client(session: Session) -> None:
    assert session.orphans == set()


def test_unreachable_database_gives_a_generic_internal_error(e2e_data: ToolData) -> None:
    # The same URL with a port nothing listens on: connection refused inside the server.
    url = make_url(local_url()).set(port=1).render_as_string(hide_password=False)

    async def run() -> tuple[Call, str]:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
            transport = stdio_client(server_params(url), errlog=errlog)
            async with Client(transport, read_timeout_seconds=TOOL_TIMEOUT_SECONDS) as client:
                call = await _call(client, "get_account", {"bank": 1, "acct": "A"})
            errlog.seek(0)
            return call, errlog.read()

    call, stderr = asyncio.run(run())
    error = _error(call)
    assert error.code == "internal"
    assert "details hidden" in error.message
    parsed = make_url(url)
    for text in (call.text, stderr):
        assert url not in text
        assert f"{parsed.username}:{parsed.password}" not in text
        assert f"@{parsed.host}" not in text
        assert ":1/" not in text


@pytest.mark.parametrize(
    "env_url",
    [None, "mysql://u:secretpw@somehost/d"],
)
def test_server_exits_at_startup_without_a_valid_database_url(env_url: str | None) -> None:
    env = {"PYTHONPATH": str(REPO_ROOT)}
    if env_url is not None:
        env["DATABASE_URL"] = env_url
    done = subprocess.run(
        [sys.executable, "-m", SERVER_MARK],
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode != 0
    assert done.stdout == ""
    assert "secretpw" not in done.stderr and "somehost" not in done.stderr
    assert "cannot start" in done.stderr
