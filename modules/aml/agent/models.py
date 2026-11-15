"""What the AML agent must submit at the end of an investigation. Money is an exact decimal
string, never a float, so cited amounts can be compared exactly with the stored ones."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness.json_schema import portable_schema
from harness.models.types import ToolSpec

SUBMIT_TOOL_NAME = "submit_findings"
SUBMIT_TOOL_DESCRIPTION = (
    "Submit your findings for this alert. Calling this tool ends the investigation, so call it "
    "exactly once, after you have gathered the evidence. Every transfer id and amount you cite "
    "must come from tool results you have seen in this investigation."
)

DECIMAL_PATTERN = r"^[0-9]+(\.[0-9]+)?$"
INTEGER_PATTERN = r"^[0-9]+$"


class Typology(StrEnum):
    """Laundering pattern shapes, plus NONE for no pattern."""

    FAN_OUT = "FAN-OUT"
    FAN_IN = "FAN-IN"
    CYCLE = "CYCLE"
    SCATTER_GATHER = "SCATTER-GATHER"
    GATHER_SCATTER = "GATHER-SCATTER"
    STACK = "STACK"
    BIPARTITE = "BIPARTITE"
    RANDOM = "RANDOM"
    NONE = "NONE"  # no laundering pattern; only with close


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Side = Literal["paid", "received"]
TxnId = Annotated[int, Field(ge=1)]
Currency = Annotated[str, Field(min_length=1, max_length=40)]


def _check_decimal(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("must be a decimal number") from None
    if not number.is_finite():
        raise ValueError("must be a finite decimal number")
    return value


class CitedAmount(_Strict):
    """One amount quoted exactly as a tool returned it, on the paid or received side."""

    txn_id: TxnId
    side: Side
    value: Annotated[str, Field(pattern=DECIMAL_PATTERN, max_length=40)]
    currency: Currency

    @field_validator("value")
    @classmethod
    def _decimal(cls, value: str) -> str:
        return _check_decimal(value)


class Claim(_Strict):
    statement: Annotated[str, Field(min_length=1, max_length=400)]
    txn_ids: Annotated[list[TxnId], Field(min_length=1, max_length=50)]
    amounts: Annotated[list[CitedAmount], Field(max_length=20)] = []


class Figure(_Strict):
    """A number the narrative states. A sum is over one side and one currency; a count is the
    number of distinct cited transfers."""

    kind: Literal["sum", "count"]
    value: Annotated[str, Field(max_length=40)]
    currency: Currency | None = None
    side: Side | None = None
    txn_ids: Annotated[list[TxnId], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def _by_kind(self) -> Self:
        if self.kind == "sum":
            if self.currency is None or self.side is None:
                raise ValueError("a sum needs a currency and a side")
            if not _matches(DECIMAL_PATTERN, self.value):
                raise ValueError("a sum value must be a decimal number")
            _check_decimal(self.value)
        else:
            if self.currency is not None:
                raise ValueError("a count has no currency")
            if not _matches(INTEGER_PATTERN, self.value):
                raise ValueError("a count value must be a whole number")
        return self


def _matches(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


class AmlFindings(_Strict):
    alert_id: Annotated[str, Field(pattern=r"^AL-[0-9a-f]{12}$")]
    recommendation: Literal["escalate", "close"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]  # probability the alert is real laundering
    typology: Typology
    narrative: Annotated[str, Field(min_length=1, max_length=1200)]
    claims: Annotated[list[Claim], Field(min_length=1, max_length=10)]
    figures: Annotated[list[Figure], Field(max_length=10)] = []

    @model_validator(mode="after")
    def _decision_rules(self) -> Self:
        if self.typology is Typology.NONE and self.recommendation != "close":
            raise ValueError("typology NONE is only allowed with close")
        if self.recommendation == "escalate" and self.confidence < 0.5:
            raise ValueError("escalate needs a confidence of at least 0.5")
        if self.recommendation == "close" and self.confidence >= 0.5:
            raise ValueError("close needs a confidence below 0.5")
        return self


def submit_findings_tool_spec() -> ToolSpec:
    """The final-answer tool. Its input schema is AmlFindings in a provider-portable form."""
    return ToolSpec(
        name=SUBMIT_TOOL_NAME,
        description=SUBMIT_TOOL_DESCRIPTION,
        input_schema=portable_schema(AmlFindings.model_json_schema()),
    )
