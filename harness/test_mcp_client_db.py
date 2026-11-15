"""The MCP client against the real AML tools server over stdio.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only."""

import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

from harness.mcp_client import ToolOutcome, ToolSession
from tools.aml.caps import TOOL_TIMEOUT_SECONDS
from tools.aml.launch import server_params
from tools.aml.query_registry import QUERY_REGISTRY, Samples

SERVER_MARK = "tools.aml.server"


def _child_server_pids() -> list[int]:
    """Server processes started by this test process (Linux /proc)."""
    me = os.getpid()
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except (OSError, IndexError):
            continue
        if int(stat[1]) == me and SERVER_MARK in cmdline:
            found.append(int(entry.name))
    return found


def test_lists_tools_and_calls_them(harness_test_url: str, harness_samples: Samples) -> None:
    async def run() -> tuple[list[str], dict[str, ToolOutcome]]:
        out: dict[str, ToolOutcome] = {}
        with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
            async with ToolSession(
                server_params(harness_test_url),
                read_timeout_seconds=TOOL_TIMEOUT_SECONDS,
                errlog=errlog,
            ) as s:
                names = [t.name for t in s.tools]
                assert all(t.description and t.input_schema for t in s.tools)
                out["describe"] = await s.call("describe_detector", {"detector": "cycle"})
                alert_args = {"alert_id": harness_samples.alert_id}
                out["alert"] = await s.call("get_alert", alert_args)
                out["missing"] = await s.call("get_transaction", {"txn_id": 999_999_999})
                out["invalid"] = await s.call("get_transaction", {"txn_id": "x"})
                out["bad_detector"] = await s.call("describe_detector", {"detector": "x"})
        return names, out

    names, out = asyncio.run(run())
    assert len(names) == 9
    assert sorted(names) == sorted(e.name for e in QUERY_REGISTRY)

    describe = out["describe"]
    assert describe.ok and describe.error_code is None and not describe.session_dead
    assert json.loads(describe.content)["detector"] == "cycle"
    assert describe.size == len(describe.content) and describe.duration_ms >= 0

    alert = out["alert"]
    assert alert.ok, alert.content
    data = json.loads(alert.content)
    assert data["alert_id"] == harness_samples.alert_id
    assert alert.truncated == data["transfers"]["truncated"]
    expected = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert alert.content == expected

    missing = out["missing"]
    assert not missing.ok and missing.error_code == "not_found"
    assert json.loads(missing.content)["code"] == "not_found"

    for key in ("invalid", "bad_detector"):
        outcome = out[key]
        assert not outcome.ok and outcome.error_code == "invalid_argument", outcome.content
        assert not outcome.session_dead
    assert "txn_id" in json.loads(out["invalid"].content)["message"]


def test_timeout_is_a_typed_error(harness_test_url: str) -> None:
    async def run() -> ToolOutcome:
        params = server_params(harness_test_url)
        with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
            async with ToolSession(params, read_timeout_seconds=1e-6, errlog=errlog) as s:
                return await s.call("describe_detector", {"detector": "cycle"})

    outcome = asyncio.run(run())
    assert not outcome.ok and outcome.error_code == "timeout"
    assert not outcome.session_dead


def test_dead_server_is_reported(harness_test_url: str) -> None:
    async def run() -> tuple[ToolOutcome, ToolOutcome]:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
            async with ToolSession(
                server_params(harness_test_url),
                read_timeout_seconds=TOOL_TIMEOUT_SECONDS,
                errlog=errlog,
            ) as s:
                pids = _child_server_pids()
                assert pids, "the server process was not found"
                for pid in pids:
                    os.kill(pid, signal.SIGKILL)
                await asyncio.sleep(0.5)
                first = await s.call("describe_detector", {"detector": "cycle"})
                second = await s.call("describe_detector", {"detector": "cycle"})
        return first, second

    first, second = asyncio.run(run())
    for outcome in (first, second):
        assert not outcome.ok and outcome.session_dead
        assert outcome.error_code == "internal"
        assert "details hidden" in json.loads(outcome.content)["message"]
