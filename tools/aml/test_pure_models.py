"""Pure tests: caps, input validation and the output models. No database."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from tools.aml import caps, models
from tools.aml.models import (
    AccountHistoryInput,
    AccountInput,
    AlertIdInput,
    AlertTransactionsInput,
    CounterpartiesInput,
    Money,
    TransactionInput,
)

FORBIDDEN_FIELDS = {"is_laundering", "laundering", "attempt_id", "attempt_type"}


def test_caps_values() -> None:
    assert caps.DEFAULT_ROWS == 25
    assert caps.MAX_ROWS == 50
    assert caps.MAX_RESPONSE_CHARS == 16000
    assert caps.MAX_COUNTERPARTIES == 20
    assert caps.MAX_GRAPH_HOPS == 2
    assert caps.DEFAULT_GRAPH_HOPS == 1
    assert caps.MAX_GRAPH_NODES == 40
    assert caps.MAX_GRAPH_EDGES == 120
    assert caps.MAX_PATTERN_TXNS == 2000
    assert caps.TOOL_TIMEOUT_SECONDS == 10


def test_no_model_has_a_forbidden_field() -> None:
    seen = 0
    for obj in vars(models).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            seen += 1
            assert not FORBIDDEN_FIELDS & set(obj.model_fields), obj.__name__
    assert seen > 10


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bank": -1, "acct": "ABC"},
        {"bank": 1, "acct": ""},
        {"bank": 1, "acct": "a" * 33},
        {"bank": 1, "acct": "ab-c"},
        {"bank": 1, "acct": "abc\n"},
        {"bank": "1", "acct": "abc"},
        {"bank": True, "acct": "abc"},
        {"bank": 1, "acct": "abc", "extra": 1},
    ],
)
def test_account_input_rejects(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AccountInput(**kwargs)


def test_account_input_accepts() -> None:
    assert AccountInput(bank=0, acct="8000EBD30").acct == "8000EBD30"


@pytest.mark.parametrize(
    "alert_id", ["", "AL-123", "al-0123456789ab", "AL-0123456789AB", "AL-0123456789abc", "x"]
)
def test_alert_id_rejects(alert_id: str) -> None:
    with pytest.raises(ValidationError):
        AlertIdInput(alert_id=alert_id)


def test_alert_id_accepts() -> None:
    assert AlertIdInput(alert_id="AL-0123456789ab").alert_id == "AL-0123456789ab"


@pytest.mark.parametrize("txn_id", [0, -5, "3", 1.5])
def test_txn_id_rejects(txn_id: Any) -> None:
    with pytest.raises(ValidationError):
        TransactionInput(txn_id=txn_id)


def test_limit_bounds_and_default() -> None:
    assert AlertTransactionsInput(alert_id="AL-0123456789ab").limit == caps.DEFAULT_ROWS
    assert AlertTransactionsInput(alert_id="AL-0123456789ab", limit=caps.MAX_ROWS).limit == 50
    for bad in (0, -1, caps.MAX_ROWS + 1):
        with pytest.raises(ValidationError):
            AlertTransactionsInput(alert_id="AL-0123456789ab", limit=bad)


def test_direction_values() -> None:
    assert AccountHistoryInput(bank=1, acct="a").direction == "both"
    assert CounterpartiesInput(bank=1, acct="a", direction="in").direction == "in"
    with pytest.raises(ValidationError):
        AccountHistoryInput(bank=1, acct="a", direction="sideways")  # type: ignore[arg-type]


def test_window_order() -> None:
    early, late = datetime(2022, 9, 1), datetime(2022, 9, 2)
    assert AccountHistoryInput(bank=1, acct="a", start=early, end=late).start == early
    assert AccountHistoryInput(bank=1, acct="a", start=early, end=early).end == early
    with pytest.raises(ValidationError):
        AccountHistoryInput(bank=1, acct="a", start=late, end=early)
    with pytest.raises(ValidationError):
        CounterpartiesInput(bank=1, acct="a", start=late, end=early)


def test_aware_datetimes_become_naive_utc() -> None:
    inp = AccountHistoryInput(bank=1, acct="a", start=datetime(2022, 9, 1, tzinfo=UTC))
    assert inp.start == datetime(2022, 9, 1)
    assert inp.start is not None and inp.start.tzinfo is None


def test_cursor_must_be_nonempty() -> None:
    with pytest.raises(ValidationError):
        AlertTransactionsInput(alert_id="AL-0123456789ab", cursor="")


def test_money_is_an_exact_string_without_exponent() -> None:
    from decimal import Decimal

    assert Money.of(Decimal("12.340000"), "USD").value == "12.340000"
    assert Money.of(Decimal("1E+3"), "EUR").value == "1000"
    assert Money.of(Decimal("0.000001"), "EUR").value == "0.000001"
    with pytest.raises(ValidationError):
        Money(value="1e5", currency="USD")
