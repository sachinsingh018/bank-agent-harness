"""Pure tests for the write path: canonical JSON, the event hash, the write URL, the records
CLI setup checks, the runner's recorder hook and the tools server env. No database."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from sqlalchemy import create_engine

from harness import records
from harness.models.scripted_adapter import ScriptedAdapter
from harness.models.types import ToolSpec
from harness.module_spec import ModuleSpec
from harness.prompts import PromptRef
from harness.records import canonical_json, event_hash, get_write_engine
from harness.runner import run_case
from harness.trace import CaseResult
from modules.aml.agent.spec import AML_SPEC
from tests_support.records_db import RecordsSnapshot, remove_since

SECRET = "s3cr3t-write-pw"
AT = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


def test_canonical_json_fixed_form() -> None:
    body = {"b": 1, "a": [Decimal("1.50"), "é"], "c": {"z": None, "y": True}}
    assert canonical_json(body) == '{"a":["1.50","\\u00e9"],"b":1,"c":{"y":true,"z":null}}'


def test_canonical_json_datetimes_are_utc_iso() -> None:
    plus_two = timezone(timedelta(hours=2))
    local = datetime(2026, 1, 2, 5, 4, 5, 678901, tzinfo=plus_two)
    assert canonical_json(local) == canonical_json(AT) == '"2026-01-02T03:04:05.678901+00:00"'
    with pytest.raises(TypeError):
        canonical_json(datetime(2026, 1, 2))
    with pytest.raises(ValueError):
        canonical_json(float("nan"))
    with pytest.raises(TypeError):
        canonical_json(object())


def test_event_hash_is_sha256_of_canonical_fields() -> None:
    payload = {"status": "proposed", "confidence": 0.25}
    got = event_hash("abc", AT, "agent-runner", "proposal_created", "AL-1", "r-1", payload)
    expected_body = (
        '{"actor":"agent-runner","at":"2026-01-02T03:04:05.678901+00:00","case_id":"AL-1",'
        '"event":"proposal_created","payload":{"confidence":0.25,"status":"proposed"},'
        '"prev_hash":"abc","run_id":"r-1"}'
    )
    assert got == hashlib.sha256(expected_body.encode()).hexdigest()
    assert len(got) == 64


def test_event_hash_changes_with_every_field() -> None:
    base = ("p", AT, "agent-runner", "proposal_created", "AL-1", "r-1", {"k": 1})
    first = event_hash(*base)
    variants = [
        (None, *base[1:]),
        (base[0], AT + timedelta(microseconds=1), *base[2:]),
        (*base[:2], "human:ann", *base[3:]),
        (*base[:3], "human_review_created", *base[4:]),
        (*base[:4], "AL-2", *base[5:]),
        (*base[:5], None, base[6]),
        (*base[:6], {"k": 2}),
    ]
    hashes = {event_hash(*v) for v in variants}
    assert first not in hashes and len(hashes) == len(variants)


def test_write_engine_reads_only_its_own_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_WRITE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    with pytest.raises(RuntimeError, match="HARNESS_WRITE_DATABASE_URL is not set"):
        get_write_engine()
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", f"postgresql://u:{SECRET}@host/db")
    with pytest.raises(RuntimeError) as exc:
        get_write_engine()
    assert SECRET not in str(exc.value)


def test_verify_cli_refuses_remote_and_hides_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "HARNESS_WRITE_DATABASE_URL", f"postgresql+psycopg://u:{SECRET}@example.com/db"
    )
    assert records.main(["verify"]) == 2
    out = capsys.readouterr().out
    assert "target: remote database" in out and "--allow-remote" in out
    assert SECRET not in out and "example.com" not in out
    monkeypatch.delenv("HARNESS_WRITE_DATABASE_URL")
    assert records.main(["verify"]) == 2
    assert "HARNESS_WRITE_DATABASE_URL is not set" in capsys.readouterr().out


def test_tools_server_env_never_gets_the_write_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/w")
    params = AML_SPEC.server_params("postgresql+psycopg://u:p@db:5432/r")
    assert params.env is not None
    assert set(params.env) == {"DATABASE_URL", "PYTHONPATH"}
    assert not any("HARNESS_WRITE" in key for key in params.env)
    assert "postgresql+psycopg://u:p@db:5432/w" not in params.env.values()


def _missing_server(url: str) -> StdioServerParameters:
    return StdioServerParameters(command="/nonexistent/tools-server", args=[])


def _spec(tmp_path: Path) -> ModuleSpec:
    prompt = tmp_path / "system_v1.md"
    prompt.write_text("system", encoding="utf-8")
    return ModuleSpec(
        name="test",
        server_params=_missing_server,
        tool_timeout_seconds=1.0,
        prompt=PromptRef(prompt, "v1"),
        output_model=ToolSpec,
        submit_tool=ToolSpec(name="submit", description="d", input_schema={}),
        verifiers=(),
        eval_dataset=Path("unused.json"),
        build_case_message=lambda case_id: case_id,
        make_lookup_factory=lambda url: lambda: None,
    )


def test_recorder_gets_the_result(tmp_path: Path) -> None:
    seen: list[CaseResult] = []

    def recorder(result: CaseResult) -> str:
        seen.append(result)
        return "run-1"

    result = asyncio.run(
        run_case(
            "C-1",
            _spec(tmp_path),
            "scripted",
            database_url="unused",
            adapter=ScriptedAdapter([]),
            recorder=recorder,
        )
    )
    assert seen == [result]


def test_recording_failure_keeps_the_outcome_and_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def recorder(result: CaseResult) -> str:
        raise RuntimeError(f"connection to {SECRET} failed")

    result = asyncio.run(
        run_case(
            "C-1",
            _spec(tmp_path),
            "scripted",
            database_url="unused",
            adapter=ScriptedAdapter([]),
            recorder=recorder,
        )
    )
    assert result.status == "human_review" and result.reason == "tool_failure"
    err = capsys.readouterr().err
    assert "recording case C-1 failed (RuntimeError)" in err
    assert SECRET not in err


def test_write_engine_refuses_a_remote_url_unless_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HARNESS_WRITE_DATABASE_URL", f"postgresql+psycopg://u:{SECRET}@example.com/db"
    )
    with pytest.raises(RuntimeError, match="--allow-remote") as exc:
        get_write_engine()
    assert SECRET not in str(exc.value) and "example.com" not in str(exc.value)
    engine = get_write_engine(allow_remote=True)  # lazy: no connection is made
    engine.dispose()
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/w")
    get_write_engine().dispose()


def test_cleanup_helper_refuses_a_remote_engine() -> None:
    engine = create_engine(f"postgresql+psycopg://u:{SECRET}@example.com/db")
    snap = RecordsSnapshot(0, frozenset(), frozenset())
    with pytest.raises(RuntimeError, match="local test database"):
        remove_since(engine, snap)
    engine.dispose()


def test_run_digest_is_stable_across_money_scale_and_ignores_ids() -> None:
    run = {"run_id": "r", "cost_usd": Decimal("0.1"), "findings": {"a": 1}}
    stored = {"run_id": "r", "cost_usd": Decimal("0.100000"), "findings": {"a": 1}}
    calls = [{"id": 1, "run_id": "r", "cost_usd": Decimal("0")}]
    stored_calls = [{"id": 7, "run_id": "r", "cost_usd": Decimal("0.000000")}]
    assert records.run_digest(run, calls) == records.run_digest(stored, stored_calls)
    changed = {**stored, "findings": {"a": 2}}
    assert records.run_digest(changed, stored_calls) != records.run_digest(run, calls)
    assert records.usd("0.0000005") == Decimal("0.000001")
