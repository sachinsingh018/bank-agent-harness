"""Pure tests for the approval queue: reviewer auth, the decision body and small helpers."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api import deps
from apps.api.corrections import CorrectionIn
from apps.api.deps import ReviewerSettings, valid_reviewer_name
from apps.api.queue import (
    DISABLED,
    FORBIDDEN,
    MAX_NOTE,
    DecisionRequest,
    check_reviewer,
    cited_txn_ids,
    decode_cursor,
    encode_cursor,
    read_tool_calls,
)

ON = ReviewerSettings(token="s3cret-token", name="Ann Lee")


def _status(settings: ReviewerSettings, token: str | None) -> tuple[int, str]:
    with pytest.raises(HTTPException) as info:
        check_reviewer(settings, token)
    return info.value.status_code, str(info.value.detail)


def test_matching_token_returns_the_reviewer_name() -> None:
    assert check_reviewer(ON, "s3cret-token") == "Ann Lee"


def test_missing_or_wrong_token_is_forbidden() -> None:
    assert _status(ON, None) == (403, FORBIDDEN)
    assert _status(ON, "") == (403, FORBIDDEN)
    assert _status(ON, "s3cret-tokeN") == (403, FORBIDDEN)


def test_decisions_disabled_without_token_or_valid_name() -> None:
    for settings in (
        ReviewerSettings(token=None, name="ann"),
        ReviewerSettings(token="t", name=None),
        ReviewerSettings(token=None, name=None),
        ReviewerSettings(token="t", name="ann<script>"),
        ReviewerSettings(token="t", name="x" * 41),
        ReviewerSettings(token="t", name="   "),
    ):
        assert _status(settings, "t") == (403, DISABLED)


def test_reviewer_name_rule() -> None:
    assert valid_reviewer_name("ann.lee_2-b c")
    assert not valid_reviewer_name("ann:lee")
    assert not valid_reviewer_name("")


def test_decision_body_rules() -> None:
    assert DecisionRequest(decision="approve").note is None
    assert DecisionRequest(decision="approve", note="  ").note is None
    assert DecisionRequest(decision="reject", note=" not enough evidence ").note == (
        "not enough evidence"
    )
    for bad in (
        {"decision": "reject"},
        {"decision": "reject", "note": "   "},
        {"decision": "edit", "note": "x"},
        {"decision": "approve", "note": "x" * (MAX_NOTE + 1)},
        {"decision": "approve", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            DecisionRequest.model_validate(bad)
    assert DecisionRequest(decision="reject", note="x" * MAX_NOTE).note is not None


def test_cursor_round_trip_and_bad_cursor() -> None:
    at = datetime(2026, 11, 16, 10, 0, 0, 123456, tzinfo=UTC)
    assert decode_cursor(encode_cursor(at, "run-1")) == (at, "run-1")
    for bad in ("not base64!!", "bm90IGpzb24=", encode_cursor(at, "r").replace("A", "")):
        with pytest.raises(HTTPException) as info:
            decode_cursor(bad)
        assert info.value.status_code == 422


def test_cited_txn_ids_from_claims_amounts_and_figures() -> None:
    findings = {
        "claims": [
            {"txn_ids": [5, 3], "amounts": [{"txn_id": 9}, {"txn_id": True}]},
            {"txn_ids": [3, -1, "7"]},
        ],
        "figures": [{"txn_ids": [11, 5]}],
    }
    assert cited_txn_ids(findings) == [3, 5, 9, 11]
    assert cited_txn_ids(None) == []


def test_read_tool_calls(tmp_path: Path) -> None:
    call = {
        "index": 0,
        "step": 1,
        "name": "get_alert",
        "arguments": {"alert_id": "AL-1"},
        "ok": True,
        "error_code": None,
        "duration_ms": 3,
        "size": 10,
        "truncated": False,
        "repeat": False,
        "content_sha256": "0" * 64,
    }
    path = tmp_path / "results.jsonl"
    lines = [
        {"case_id": "AL-2", "trace": {"tool_calls": []}},
        {"case_id": "AL-1", "trace": {"tool_calls": [call]}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    calls = read_tool_calls(str(path), "AL-1")
    assert calls is not None and [c.name for c in calls] == ["get_alert"]
    assert read_tool_calls(str(path), "AL-3") is None
    assert read_tool_calls(None, "AL-1") is None
    assert read_tool_calls(str(tmp_path / "other.json"), "AL-1") is None
    assert read_tool_calls(str(tmp_path / "missing" / "results.jsonl"), "AL-1") is None


def test_tool_engine_refuses_a_remote_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """No connection is opened: engines connect lazily and the URL host is never reached."""
    remote = "postgresql+psycopg://user:pw@example.invalid:5432/x"
    monkeypatch.setenv("DATABASE_URL", remote)
    monkeypatch.delenv(deps.ALLOW_REMOTE_ENV, raising=False)
    deps._tool_engine.cache_clear()
    try:
        with pytest.raises(HTTPException) as info:
            deps.get_tool_engine()
        assert info.value.status_code == 503
        assert info.value.detail == deps.NOT_CONFIGURED
        assert "example.invalid" not in str(info.value.detail)

        monkeypatch.setenv(deps.ALLOW_REMOTE_ENV, "1")
        engine = deps.get_tool_engine()
        engine.dispose()
    finally:
        deps._tool_engine.cache_clear()


def test_tool_engine_unset_url_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    deps._tool_engine.cache_clear()
    try:
        with pytest.raises(HTTPException) as info:
            deps.get_tool_engine()
        assert info.value.status_code == 503
    finally:
        deps._tool_engine.cache_clear()


EDITS = {"recommendation": "close", "confidence": 0.2, "typology": "NONE", "narrative": "Fine."}


def test_approve_with_edits_needs_a_note_and_edits() -> None:
    body = DecisionRequest.model_validate(
        {"decision": "approve_with_edits", "note": " why ", "edits": EDITS}
    )
    assert body.note == "why" and body.edits is not None
    for bad in (
        {"decision": "approve_with_edits", "edits": EDITS},
        {"decision": "approve_with_edits", "note": "why"},
        {"decision": "approve", "edits": EDITS},
        {"decision": "approve_with_edits", "note": "why", "edits": {**EDITS, "extra": 1}},
        {"decision": "approve_with_edits", "note": "why", "edits": {**EDITS, "confidence": 2}},
    ):
        with pytest.raises(ValidationError):
            DecisionRequest.model_validate(bad)


def test_correction_rule_refuses_dates_months_and_years() -> None:
    for rule in (
        "Activity after 2022 is noise.",
        "Ignore anything from Sept onwards.",
        "Transfers on 2022-09-11 look odd.",
        "Transfers on 11/09/2022 look odd.",
        "Transfers on 5 May look odd.",
        "Transfers in DECEMBER look odd.",
    ):
        with pytest.raises(ValidationError, match="decision 0006"):
            CorrectionIn(kind="other", rule=rule)
    ok = CorrectionIn(kind="typology", rule="  Payroll may look like fan-out, amounts 10.50.  ")
    assert ok.rule == "Payroll may look like fan-out, amounts 10.50."
    for rule in ("short", "x" * 501):
        with pytest.raises(ValidationError):
            CorrectionIn(kind="other", rule=rule)
    with pytest.raises(ValidationError):
        CorrectionIn.model_validate({"kind": "date", "rule": "A long enough lesson."})
