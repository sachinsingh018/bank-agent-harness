"""Pure tests of how the MCP client turns server results into outcomes. No server, no database."""

import json

from harness.mcp_client import INTERNAL_MESSAGE, _dump, _error_payload, _truncated


def test_server_tool_error_json_is_parsed() -> None:
    text = 'Error executing tool get_alert: {"code":"not_found","message":"alert not found"}'
    payload = _error_payload(text)
    assert payload.code == "not_found"
    assert payload.message == "alert not found"


def test_sdk_argument_error_keeps_fields_but_not_input_values() -> None:
    text = (
        "Error executing tool get_account: 2 validation errors for get_accountArguments\n"
        "acct\n"
        "  String should match pattern '^[A-Za-z0-9]{1,32}$' [type=string_pattern_mismatch, "
        "input_value='SECRETVALUE x', input_type=str]\n"
        "    For further information visit https://errors.pydantic.dev/2.14/v/string_pattern\n"
        "bank\n"
        "  Input should be a valid integer [type=int_type, input_value='OTHERSECRET', "
        "input_type=str]\n"
        "    For further information visit https://errors.pydantic.dev/2.14/v/int_type"
    )
    payload = _error_payload(text)
    assert payload.code == "invalid_argument"
    assert "acct: String should match pattern" in payload.message
    assert "bank: Input should be a valid integer" in payload.message
    for leaked in ("SECRETVALUE", "OTHERSECRET", "input_value", "input_type", "https", "type="):
        assert leaked not in payload.message


def test_unknown_tool_is_invalid_argument_without_echo() -> None:
    payload = _error_payload("Unknown tool: nope")
    assert payload.code == "invalid_argument"
    assert "nope" not in payload.message


def test_validation_error_words_inside_other_text_are_internal() -> None:
    texts = [
        "Error executing tool get_alert: database said: 1 validation error for X at db.internal",
        "Error executing tool get_alert: it broke\n1 validation error for get_alertArguments",
        "Error executing tool get_alert: 1 validation error for get_alertArguments\n"
        "alert_id\n  Field required [type=missing]\nextra secret line",
        "Error executing tool get_alert: 1 validation error for otherArguments\n"
        "alert_id\n  Field required [type=missing]",
        "Error executing tool get_alert: 2 validation errors for get_alertArguments\n"
        "alert_id\n  Field required [type=missing]",
        'Error executing tool get_alert: oops {"code":"not_found","message":"x"}',
    ]
    for text in texts:
        payload = _error_payload(text)
        assert payload.code == "internal", text
        assert payload.message == INTERNAL_MESSAGE


def test_unknown_error_text_is_hidden_behind_a_generic_message() -> None:
    payload = _error_payload("Traceback: secret connection string postgres://u:p@h/d")
    assert payload.code == "internal"
    assert payload.message == INTERNAL_MESSAGE
    odd = _error_payload('{"code":"weird","message":"x"}')
    assert odd.code == "internal"


def test_truncated_flag_top_level_or_nested_transfers() -> None:
    assert _truncated({"truncated": True}) is True
    assert _truncated({"transfers": {"truncated": True}}) is True
    assert _truncated({"transfers": {"truncated": False}}) is False
    assert _truncated({"items": []}) is False
    assert _truncated(None) is False


def test_dump_is_sorted_and_round_trips() -> None:
    text = _dump({"b": 1, "a": {"d": 2, "c": 3}})
    assert text == '{"a":{"c":3,"d":2},"b":1}'
    assert json.loads(text) == {"a": {"c": 3, "d": 2}, "b": 1}
