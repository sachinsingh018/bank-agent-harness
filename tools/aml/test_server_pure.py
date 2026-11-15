"""Server pieces that need no database: descriptions, launch parameters, schema snapshot."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from tools.aml.descriptions import DESCRIPTIONS_PATH, DescriptionsError, load_descriptions
from tools.aml.launch import REPO_ROOT, server_params
from tools.aml.query_registry import QUERY_REGISTRY
from tools.aml.schema_snapshot import SNAPSHOT_PATH, live_snapshot, render
from tools.aml.server import SERVER_NAME, build_server

NAMES = [entry.name for entry in QUERY_REGISTRY]
DASHES = (chr(0x2014), chr(0x2013))


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "d.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_descriptions_file_is_complete_versioned_and_plain() -> None:
    raw = DESCRIPTIONS_PATH.read_text(encoding="utf-8")
    assert not any(dash in raw for dash in DASHES)
    descriptions = load_descriptions(NAMES)
    assert descriptions.version == "v1"
    assert list(descriptions.tools) == NAMES


def test_descriptions_state_the_caps_and_the_warnings() -> None:
    tools = load_descriptions(NAMES).tools
    for name in NAMES[:2] + ["get_account_history"]:
        assert "truncated" in tools[name] and "next_cursor" in tools[name]
        assert "16,000 characters" in tools[name]
    # get_alert has no limit parameter: its first page is fixed at 25 transfers.
    assert "first page always holds up to 25 transfers" in tools["get_alert"]
    assert "limit of up to 50" in tools["get_alert"]
    for name in NAMES[1:2] + ["get_account_history"]:
        assert "25 rows by default and 50 at most" in tools[name]
    assert "top 20" in tools["get_counterparties"]
    assert "2 hops" in tools["get_transaction_graph"]
    assert "40 accounts and 120 transfers" in tools["get_transaction_graph"]
    pattern = tools["run_pattern_check"]
    assert "2,000 transfers" in pattern
    assert "same detectors" in pattern
    assert "more than 4 accounts may be missed" in pattern
    for name in NAMES:
        if name != "describe_detector":
            assert "never summed across currencies" in tools[name], name


def test_loader_fails_when_a_tool_has_no_description(tmp_path: Path) -> None:
    tools = {name: "text" for name in NAMES[1:]}
    with pytest.raises(DescriptionsError, match="without a description"):
        load_descriptions(NAMES, _write(tmp_path, {"version": "v1", "tools": tools}))


def test_loader_fails_when_a_description_has_no_tool(tmp_path: Path) -> None:
    tools = {name: "text" for name in NAMES} | {"get_labels": "text"}
    with pytest.raises(DescriptionsError, match="without a tool"):
        load_descriptions(NAMES, _write(tmp_path, {"version": "v1", "tools": tools}))


def test_loader_fails_on_empty_text_or_missing_version(tmp_path: Path) -> None:
    tools = {name: "text" for name in NAMES} | {NAMES[0]: "  "}
    with pytest.raises(DescriptionsError, match="empty"):
        load_descriptions(NAMES, _write(tmp_path, {"version": "v1", "tools": tools}))
    with pytest.raises(Exception, match="version"):
        load_descriptions(NAMES, _write(tmp_path, {"tools": {n: "t" for n in NAMES}}))


def test_server_has_the_name_and_the_descriptions_version() -> None:
    server = build_server()
    assert server.name == SERVER_NAME == "aml-investigation"
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    descriptions = load_descriptions(NAMES)
    for name in NAMES:
        assert tools[name].description == descriptions.tools[name]


def test_launch_env_we_pass_is_exactly_the_database_url_and_pythonpath() -> None:
    # The MCP client adds only its own safe basics (HOME, LOGNAME, PATH, SHELL, TERM, USER).
    params = server_params("postgresql+psycopg://u:p@host:5432/d")
    assert params.env is not None
    assert set(params.env) == {"DATABASE_URL", "PYTHONPATH"}
    assert params.env["DATABASE_URL"] == "postgresql+psycopg://u:p@host:5432/d"
    assert params.env["PYTHONPATH"] == str(REPO_ROOT)
    assert params.args == ["-m", "tools.aml.server"]
    assert Path(str(params.cwd)) == REPO_ROOT
    assert (REPO_ROOT / "tools" / "aml" / "server.py").exists()


def test_tool_schemas_match_the_snapshot() -> None:
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    message = (
        "tool changes must be deliberate: regenerate with "
        "`uv run python -m tools.aml.schema_snapshot --write` and review the diff"
    )
    assert render(live_snapshot()) == expected, message


def test_snapshot_holds_every_tool_and_the_version() -> None:
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert snapshot["descriptions_version"] == "v1"
    assert sorted(t["name"] for t in snapshot["tools"]) == sorted(NAMES)
    for tool in snapshot["tools"]:
        assert tool["description"] and tool["input_schema"] and tool["output_schema"]
    assert not re.search(r"is_laundering|attempt_id|attempt_type", SNAPSHOT_PATH.read_text())
