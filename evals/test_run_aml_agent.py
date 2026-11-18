"""Runner tests: scripted policy, setup errors and the remote database refusal. No database."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evals import aml_agent_score, run_aml_agent
from evals.run_aml_agent import SetupError, check_target, is_local_url, run_dataset
from evals.scripted_policy import NO_ALERT_TEXT, ScriptedAmlPolicy
from harness.models.types import Message, ModelRequest, TextBlock, ToolResultBlock
from modules.aml.agent.models import REVIEW_TOOL_NAME, SUBMIT_TOOL_NAME, AmlFindings, AmlReview
from modules.aml.agent.spec import AML_SPEC, build_case_message

REMOTE = "postgresql+psycopg://user:secretpw@example.com:5432/somedb"
SMOKE = Path(__file__).parent / "datasets" / "aml_alerts_smoke_v2.json"
ALERT_ID = "AL-0123456789ab"


def _req(messages: list[Message]) -> ModelRequest:
    return ModelRequest(system="s", messages=messages, tools=[], max_output_tokens=100)


def _alert_body() -> dict[str, object]:
    return {
        "alert_id": ALERT_ID,
        "detector": "fan_out",
        "transfers": {
            "items": [
                {
                    "txn_id": 42,
                    "amount_paid": {"value": "1234.50", "currency": "US Dollar"},
                    "amount_received": {"value": "1234.50", "currency": "US Dollar"},
                }
            ]
        },
    }


def test_scripted_policy_asks_for_the_alert_then_submits() -> None:
    policy = ScriptedAmlPolicy()
    first = Message(role="user", blocks=[TextBlock(text=build_case_message(ALERT_ID))])
    turn1 = asyncio.run(policy.complete(_req([first])))
    calls = turn1.message.tool_calls()
    assert [(c.name, c.arguments) for c in calls] == [("get_alert", {"alert_id": ALERT_ID})]
    result = Message(
        role="user",
        blocks=[ToolResultBlock(tool_call_id=calls[0].id, content=json.dumps(_alert_body()))],
    )
    turn2 = asyncio.run(policy.complete(_req([first, turn1.message, result])))
    (submit,) = turn2.message.tool_calls()
    assert submit.name == SUBMIT_TOOL_NAME
    findings = AmlFindings.model_validate(submit.arguments)
    assert findings.recommendation == "close" and findings.confidence == 0.1
    assert findings.typology.value == "NONE"
    amount = findings.claims[0].amounts[0]
    assert (amount.txn_id, amount.value, amount.currency) == (42, "1234.50", "US Dollar")


def test_scripted_policy_approves_as_the_checker() -> None:
    reviewer = AML_SPEC.reviewer
    assert reviewer is not None
    first = Message(role="user", blocks=[TextBlock(text="review AL-0123456789ab")])
    req = ModelRequest(
        system="s", messages=[first], tools=[reviewer.submit_tool], max_output_tokens=100
    )
    (call,) = asyncio.run(ScriptedAmlPolicy().complete(req)).message.tool_calls()
    assert call.name == REVIEW_TOOL_NAME
    assert AmlReview.model_validate(call.arguments).verdict == "approve"


def test_reviewer_profile_with_no_review_is_a_setup_error(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="--no-review"):
        run_dataset(
            SMOKE,
            "scripted",
            "postgresql+psycopg://u:p@db:5432/x",
            out_dir=tmp_path,
            review=False,
            reviewer_profile_name="scripted",
        )


def test_scripted_policy_gives_up_on_a_failed_alert_lookup() -> None:
    first = Message(role="user", blocks=[TextBlock(text=build_case_message(ALERT_ID))])
    error = Message(
        role="user", blocks=[ToolResultBlock(tool_call_id="a_0", content="{}", is_error=True)]
    )
    out = asyncio.run(ScriptedAmlPolicy().complete(_req([first, error])))
    assert out.message.text() == NO_ALERT_TEXT and not out.message.tool_calls()


def test_target_check() -> None:
    assert is_local_url("postgresql+psycopg://postgres:postgres@db:5432/harness")
    assert not is_local_url(REMOTE)
    check_target(REMOTE, allow_remote=True)
    with pytest.raises(SetupError, match="--allow-remote"):
        check_target(REMOTE, allow_remote=False)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5432/x?host=remote.example.com",
        "postgresql+psycopg://u:p@db:5432/x?hostaddr=203.0.113.5",
        "postgresql+psycopg://u:p@localhost/x?service=prod",
        "postgresql+psycopg://u:p@db,remote.example.com/x",
        "postgresql+psycopg://u:p@db:5432,remote.example.com:5432/x",
        "postgresql+psycopg:///x",
        "postgresql+psycopg://u:p@/x",
    ],
)
def test_bypass_shapes_are_not_local(url: str) -> None:
    assert not is_local_url(url)
    with pytest.raises(SetupError, match="--allow-remote"):
        check_target(url, allow_remote=False)


def _no_connect(*args: object, **kwargs: object) -> None:
    raise AssertionError("must not get this far")


def test_runner_refuses_a_remote_database_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", REMOTE)
    monkeypatch.setattr(run_aml_agent, "run_dataset", _no_connect)
    code = run_aml_agent.main(
        ["--dataset", str(SMOKE), "--profile", "scripted", "--out-dir", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "target: remote database" in out and "--allow-remote" in out
    assert "example.com" not in out and "secretpw" not in out
    assert not list(tmp_path.iterdir())


def test_scorer_refuses_a_remote_database_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", REMOTE)
    monkeypatch.setattr(aml_agent_score, "get_engine", _no_connect)
    code = aml_agent_score.main(["--run", str(tmp_path)])
    out = capsys.readouterr().out
    assert code != 0 and "target: remote database" in out and "example.com" not in out


LOCAL = "postgresql+psycopg://postgres:postgres@db:5432/harness"


def test_runner_record_needs_the_write_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    monkeypatch.delenv("HARNESS_WRITE_DATABASE_URL", raising=False)
    monkeypatch.setattr(run_aml_agent, "run_dataset", _no_connect)
    argv = ["--dataset", str(SMOKE), "--profile", "scripted", "--out-dir", str(tmp_path)]
    assert run_aml_agent.main([*argv, "--record"]) == 2
    assert "HARNESS_WRITE_DATABASE_URL" in capsys.readouterr().out


def test_runner_record_refuses_a_remote_write_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", REMOTE)
    monkeypatch.setattr(run_aml_agent, "run_dataset", _no_connect)
    monkeypatch.setattr(run_aml_agent, "get_write_engine", _no_connect)
    argv = ["--dataset", str(SMOKE), "--profile", "scripted", "--out-dir", str(tmp_path)]
    assert run_aml_agent.main([*argv, "--record"]) == 2
    out = capsys.readouterr().out
    assert "write target: remote database" in out and "--allow-remote" in out
    assert "HARNESS_WRITE_DATABASE_URL is not local" in out
    assert "example.com" not in out and "secretpw" not in out


def test_scorer_record_refuses_a_remote_write_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    monkeypatch.setenv("HARNESS_WRITE_DATABASE_URL", REMOTE)
    monkeypatch.setattr(aml_agent_score, "get_engine", _no_connect)
    assert aml_agent_score.main(["--run", str(tmp_path), "--record"]) != 0
    out = capsys.readouterr().out
    assert "write target: remote database" in out and "example.com" not in out


def test_real_profile_without_api_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SetupError, match="ANTHROPIC_API_KEY"):
        run_dataset(
            SMOKE,
            "claude-opus",
            "postgresql+psycopg://postgres:postgres@db:5432/none",
            out_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_unknown_profile_and_bad_dataset_are_setup_errors(tmp_path: Path) -> None:
    url = "postgresql+psycopg://postgres:postgres@db:5432/none"
    with pytest.raises(SetupError, match="unknown model profile"):
        run_dataset(SMOKE, "no-such-profile", url, out_dir=tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(SetupError, match="not a valid dataset"):
        run_dataset(bad, "scripted", url, out_dir=tmp_path)
