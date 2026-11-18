"""Deterministic, schema-driven repair of tool call arguments.

Some models (often OpenAI-compatible ones) send a list or object argument as a JSON-encoded
string, for example `"claims": "[{...}]"`. Where the tool's input schema expects an array or
an object and the value is a string that parses to exactly that kind, the string is replaced
by the parsed value. Nothing else is ever changed: no number or boolean coercion, no guessing,
no fixing of broken JSON. Invalid JSON stays as it is, so validation then fails normally.

Schemas are expected to be inlined already (no `$ref`), as portable_schema produces.
"""

from __future__ import annotations

import json
from typing import Any

_KIND = {"array": list, "object": dict}


def _types(schema: dict[str, Any]) -> set[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        out = {declared}
    elif isinstance(declared, list):
        out = {t for t in declared if isinstance(t, str)}
    else:
        out = set()
    if "properties" in schema:
        out.add("object")
    if "items" in schema:
        out.add("array")
    return out


def _branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """The schema itself plus its anyOf or oneOf branches, flattened."""
    out = [schema]
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict):
                    out.extend(_branches(option))
    return out


def _branch_for(schema: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for branch in _branches(schema):
        if kind in _types(branch):
            return branch
    return None


def _join(path: str, part: str) -> str:
    return f"{path}.{part}" if path else part


def _walk(value: Any, schema: Any, path: str, repairs: list[str]) -> Any:
    if not isinstance(schema, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        for kind, opener in (("array", "["), ("object", "{")):
            if not text.startswith(opener):
                continue
            branch = _branch_for(schema, kind)
            if branch is None or _branch_for(schema, "string") is not None:
                # This kind is not expected here, or a plain string is allowed too: leave it.
                return value
            try:
                parsed = json.loads(text)
            except ValueError:
                return value
            if not isinstance(parsed, _KIND[kind]):
                return value
            repairs.append(path or "(root)")
            return _walk(parsed, branch, path, repairs)
        return value
    if isinstance(value, dict):
        branch = _branch_for(schema, "object")
        if branch is None:
            return value
        props = branch.get("properties")
        if not isinstance(props, dict):
            return value
        return {
            key: _walk(sub, props.get(key), _join(path, key), repairs) for key, sub in value.items()
        }
    if isinstance(value, list):
        branch = _branch_for(schema, "array")
        if branch is None:
            return value
        items = branch.get("items")
        return [_walk(sub, items, _join(path, str(i)), repairs) for i, sub in enumerate(value)]
    return value


def unpack_json_strings(value: Any, schema: dict[str, Any]) -> tuple[Any, list[str]]:
    """Return the value with JSON-encoded list or object strings unpacked where the schema
    expects an array or object, and the dotted paths that were changed."""
    repairs: list[str] = []
    return _walk(value, schema, "", repairs), repairs
