"""Helpers for JSON schemas sent to model providers."""

from __future__ import annotations

from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with every local `#/$defs/...` reference replaced by its definition.

    Some providers handle `$ref` poorly, so tool input schemas are sent self-contained.
    Recursive definitions are refused, since they cannot be inlined.
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def walk(node: Any, active: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(item, active) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            prefix = "#/$defs/"
            if not ref.startswith(prefix):
                raise ValueError(f"unsupported schema reference {ref!r}")
            name = ref[len(prefix) :]
            if name in active:
                raise ValueError(f"recursive schema definition {name!r}")
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, (*active, name))
        return {k: walk(v, active) for k, v in node.items() if k != "$defs"}

    result = walk(schema, ())
    assert isinstance(result, dict)
    return result


_DROPPED_KEYS = frozenset({"title", "default"})


def portable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A tool input schema that every provider accepts the same way.

    No `$ref` or `$defs`, no `title` or `default`, every object closed with
    additionalProperties false and every property listed in `required`. Optional fields keep
    their `anyOf` with null, so the model sends null instead of leaving them out.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROPPED_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # Property names are data here, so only their schemas are walked.
                out[key] = {name: walk(sub) for name, sub in value.items()}
            else:
                out[key] = walk(value)
        if out.get("type") == "object" or "properties" in out:
            out["properties"] = out.get("properties", {})
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    result = walk(inline_refs(schema))
    assert isinstance(result, dict)
    return result
