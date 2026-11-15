"""inline_refs: references replaced by their definitions, recursion refused."""

from typing import Any

import pytest

from harness.json_schema import inline_refs, portable_schema


def test_refs_are_inlined_and_defs_dropped() -> None:
    schema: dict[str, Any] = {
        "$defs": {"A": {"type": "object", "properties": {"x": {"type": "integer"}}}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}, "many": {"items": {"$ref": "#/$defs/A"}}},
    }
    out = inline_refs(schema)
    assert "$defs" not in out
    assert out["properties"]["a"] == schema["$defs"]["A"]
    assert out["properties"]["many"]["items"] == schema["$defs"]["A"]
    assert "$defs" in schema  # the input is not changed


def test_recursive_definition_is_refused() -> None:
    schema = {"$defs": {"N": {"properties": {"n": {"$ref": "#/$defs/N"}}}}, "$ref": "#/$defs/N"}
    with pytest.raises(ValueError, match="recursive"):
        inline_refs(schema)


def test_portable_schema_closes_objects_and_drops_titles_and_defaults() -> None:
    schema: dict[str, Any] = {
        "$defs": {"In": {"title": "In", "type": "object", "properties": {"x": {"type": "string"}}}},
        "title": "Out",
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "inner": {"$ref": "#/$defs/In"},
            "opt": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["title"],
    }
    out = portable_schema(schema)
    assert "title" not in out and "$defs" not in out
    assert out["required"] == ["title", "inner", "opt"]
    assert out["additionalProperties"] is False
    assert out["properties"]["title"] == {"type": "string"}  # a property named title stays
    assert out["properties"]["inner"] == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    assert out["properties"]["opt"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
