"""Schema-driven unpacking of JSON-encoded string arguments. Pure, no network."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from harness.arg_repair import unpack_json_strings
from harness.json_schema import portable_schema


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Amount(_Strict):
    value: str
    currency: str


class Claim(_Strict):
    text: str
    txn_ids: list[int]
    amounts: list[Amount]


class Note(_Strict):
    author: str


class Findings(_Strict):
    narrative: str
    claims: list[Claim]
    note: Note | None = None
    count: int


SCHEMA = portable_schema(Findings.model_json_schema())


def _claim() -> dict[str, Any]:
    return {"text": "t", "txn_ids": [1], "amounts": [{"value": "10.00", "currency": "USD"}]}


def _args(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"narrative": "n", "claims": [_claim()], "note": None, "count": 1}
    return {**base, **overrides}


def test_claims_string_is_unpacked() -> None:
    value, repairs = unpack_json_strings(_args(claims=json.dumps([_claim()])), SCHEMA)
    assert value == _args()
    assert repairs == ["claims"]
    Findings.model_validate(value)


def test_nested_amounts_string_is_unpacked() -> None:
    claim = _claim()
    claim["amounts"] = "  " + json.dumps(claim["amounts"])
    value, repairs = unpack_json_strings(_args(claims=[claim]), SCHEMA)
    assert value == _args()
    assert repairs == ["claims.0.amounts"]


def test_nested_strings_inside_an_unpacked_string() -> None:
    claim = _claim()
    claim["amounts"] = json.dumps(claim["amounts"])
    value, repairs = unpack_json_strings(_args(claims=json.dumps([claim])), SCHEMA)
    assert value == _args()
    assert repairs == ["claims", "claims.0.amounts"]


def test_any_of_null_path_is_unpacked() -> None:
    value, repairs = unpack_json_strings(_args(note='{"author": "a"}'), SCHEMA)
    assert value["note"] == {"author": "a"}
    assert repairs == ["note"]


def test_invalid_json_stays() -> None:
    bad = '[{"text": "t", '
    value, repairs = unpack_json_strings(_args(claims=bad), SCHEMA)
    assert value["claims"] == bad and repairs == []


def test_string_typed_field_is_never_changed() -> None:
    value, repairs = unpack_json_strings(_args(narrative="[note] see txn 1"), SCHEMA)
    assert value["narrative"] == "[note] see txn 1" and repairs == []
    value, repairs = unpack_json_strings(_args(narrative="[1, 2]"), SCHEMA)
    assert value["narrative"] == "[1, 2]" and repairs == []


def test_wrong_kind_is_unchanged() -> None:
    value, repairs = unpack_json_strings(_args(note="[1,2]"), SCHEMA)
    assert value["note"] == "[1,2]" and repairs == []
    value, repairs = unpack_json_strings(_args(claims='{"a": 1}'), SCHEMA)
    assert value["claims"] == '{"a": 1}' and repairs == []


def test_no_coercion_of_scalars() -> None:
    value, repairs = unpack_json_strings(_args(count="1"), SCHEMA)
    assert value["count"] == "1" and repairs == []
