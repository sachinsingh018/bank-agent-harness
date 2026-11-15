"""AmlFindings and its parts: every validation rule, the enum and the submit tool schema."""

from typing import Any

import jsonschema  # type: ignore[import-untyped]  # comes with mcp
import pytest
from pydantic import ValidationError

from harness.db.models import ATTEMPT_TYPES
from modules.aml.agent.models import (
    AmlFindings,
    CitedAmount,
    Claim,
    Figure,
    Typology,
    submit_findings_tool_spec,
)

ALERT = "AL-0123456789ab"


def _assert_portable(node: Any) -> None:
    """No $ref, $defs, title or default anywhere; every object closed with all fields required."""
    if isinstance(node, list):
        for item in node:
            _assert_portable(item)
        return
    if not isinstance(node, dict):
        return
    for key in ("$ref", "$defs", "title", "default"):
        assert key not in node, f"{key} in {node}"
    if node.get("type") == "object" or "properties" in node:
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
    for key, value in node.items():
        if key == "properties":
            for sub in value.values():
                _assert_portable(sub)
        else:
            _assert_portable(value)


def _findings(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "alert_id": ALERT,
        "recommendation": "escalate",
        "confidence": 0.8,
        "typology": "FAN-OUT",
        "narrative": "One account paid many new accounts in a short time.",
        "claims": [
            {
                "statement": "Account A paid account B.",
                "txn_ids": [1],
                "amounts": [
                    {"txn_id": 1, "side": "paid", "value": "100.50", "currency": "US Dollar"}
                ],
            }
        ],
        "figures": [
            {
                "kind": "sum",
                "value": "100.50",
                "currency": "US Dollar",
                "side": "paid",
                "txn_ids": [1],
            },
            {"kind": "count", "value": "1", "currency": None, "side": None, "txn_ids": [1]},
        ],
    }
    base.update(changes)
    return base


def test_typology_values_match_the_pattern_names_plus_none() -> None:
    assert {t.value for t in Typology} == set(ATTEMPT_TYPES) | {"NONE"}


def test_decision_rule_boundaries_accept() -> None:
    AmlFindings.model_validate(_findings(confidence=0.5))
    AmlFindings.model_validate(_findings(recommendation="close", confidence=0.49))
    AmlFindings.model_validate(_findings(recommendation="close", confidence=0.0, typology="NONE"))


def test_valid_findings_parse() -> None:
    findings = AmlFindings.model_validate(_findings())
    assert findings.typology is Typology.FAN_OUT
    assert findings.figures[1].currency is None


@pytest.mark.parametrize(
    "changes",
    [
        {"alert_id": "AL-xyz"},
        {"recommendation": "maybe"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"typology": "STRUCTURING"},
        {"narrative": "x" * 1201},
        {"narrative": ""},
        {"claims": []},
        {"claims": _findings()["claims"] * 11},
        {"figures": _findings()["figures"] * 6},
        {"extra": 1},
        {"typology": "NONE"},
        {"typology": "NONE", "recommendation": "escalate", "confidence": 0.9},
        {"recommendation": "escalate", "confidence": 0.49},
        {"recommendation": "close", "confidence": 0.5},
        {"recommendation": "close", "confidence": 0.7, "typology": "NONE"},
    ],
)
def test_findings_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AmlFindings.model_validate(_findings(**changes))


@pytest.mark.parametrize(
    "data",
    [
        {"txn_id": 1, "side": "sent", "value": "1", "currency": "Euro"},
        {"txn_id": 1, "side": "paid", "value": "1.2.3", "currency": "Euro"},
        {"txn_id": 1, "side": "paid", "value": "1e5", "currency": "Euro"},
        {"txn_id": 1, "side": "paid", "value": "NaN", "currency": "Euro"},
        {"txn_id": 1, "side": "paid", "value": 1.5, "currency": "Euro"},
        {"txn_id": 0, "side": "paid", "value": "1", "currency": "Euro"},
        {"txn_id": 1, "side": "paid", "value": "1", "currency": ""},
        {"txn_id": 1, "side": "paid", "value": "1", "currency": "Euro", "x": 1},
    ],
)
def test_cited_amount_rules(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CitedAmount.model_validate(data)


def test_cited_amount_keeps_the_exact_string() -> None:
    amount = CitedAmount(txn_id=3, side="received", value="100.00", currency="Euro")
    assert amount.value == "100.00"


@pytest.mark.parametrize(
    "data",
    [
        {"statement": "x" * 401, "txn_ids": [1]},
        {"statement": "ok", "txn_ids": []},
        {"statement": "ok", "txn_ids": list(range(1, 52))},
        {
            "statement": "ok",
            "txn_ids": [1],
            "amounts": [{"txn_id": 1, "side": "paid", "value": "1", "currency": "Euro"}] * 21,
        },
    ],
)
def test_claim_rules(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Claim.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "sum", "value": "10", "side": "paid", "txn_ids": [1]},
        {"kind": "sum", "value": "10", "currency": "Euro", "txn_ids": [1]},
        {"kind": "sum", "value": "ten", "currency": "Euro", "side": "paid", "txn_ids": [1]},
        {"kind": "count", "value": "2.5", "txn_ids": [1, 2]},
        {"kind": "count", "value": "2", "currency": "Euro", "txn_ids": [1, 2]},
        {"kind": "count", "value": "1", "txn_ids": []},
        {"kind": "mean", "value": "1", "txn_ids": [1]},
    ],
)
def test_figure_rules(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Figure.model_validate(data)


def test_submit_tool_spec_is_a_valid_self_contained_schema() -> None:
    spec = submit_findings_tool_spec()
    assert spec.name == "submit_findings"
    assert "ends the investigation" in spec.description
    schema = spec.input_schema
    jsonschema.Draft202012Validator.check_schema(schema)
    _assert_portable(schema)
    assert schema["type"] == "object"
    figures = schema["properties"]["claims"]["items"]["properties"]["amounts"]
    assert figures["type"] == "array"
    currency = schema["properties"]["figures"]["items"]["properties"]["currency"]
    assert {"type": "null"} in currency["anyOf"]
    jsonschema.validate(_findings(), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_findings(extra=1), schema)
