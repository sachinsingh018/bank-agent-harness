"""The query functions with sample-argument builders. Tests iterate over this list, and a test
fails if a public query function is missing from it."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from tools.aml import queries
from tools.aml.models import (
    AccountHistoryInput,
    AccountInput,
    AccountRef,
    AlertIdInput,
    AlertTransactionsInput,
    CounterpartiesInput,
    TransactionInput,
)


@dataclass(frozen=True)
class Samples:
    """Real values taken from the database by the test."""

    alert_id: str
    account: AccountRef
    txn_id: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class QueryEntry:
    name: str
    func: Callable[[Any, Any], BaseModel]
    build_args: Callable[[Samples], list[BaseModel]]


def _alert_args(s: Samples) -> list[BaseModel]:
    return [AlertIdInput(alert_id=s.alert_id)]


def _alert_txn_args(s: Samples) -> list[BaseModel]:
    return [
        AlertTransactionsInput(alert_id=s.alert_id),
        AlertTransactionsInput(alert_id=s.alert_id, limit=3),
    ]


def _txn_args(s: Samples) -> list[BaseModel]:
    return [TransactionInput(txn_id=s.txn_id)]


def _account_args(s: Samples) -> list[BaseModel]:
    return [AccountInput(bank=s.account.bank, acct=s.account.acct)]


def _history_args(s: Samples) -> list[BaseModel]:
    a = s.account
    return [
        AccountHistoryInput(bank=a.bank, acct=a.acct),
        AccountHistoryInput(bank=a.bank, acct=a.acct, direction="in", limit=5),
        AccountHistoryInput(bank=a.bank, acct=a.acct, direction="out", start=s.start, end=s.end),
    ]


def _counterparty_args(s: Samples) -> list[BaseModel]:
    a = s.account
    return [
        CounterpartiesInput(bank=a.bank, acct=a.acct),
        CounterpartiesInput(bank=a.bank, acct=a.acct, direction="out", start=s.start, end=s.end),
    ]


QUERY_REGISTRY: tuple[QueryEntry, ...] = (
    QueryEntry("get_alert", queries.get_alert, _alert_args),
    QueryEntry("get_alert_transactions", queries.get_alert_transactions, _alert_txn_args),
    QueryEntry("get_transaction", queries.get_transaction, _txn_args),
    QueryEntry("get_account", queries.get_account, _account_args),
    QueryEntry("get_account_history", queries.get_account_history, _history_args),
    QueryEntry("get_counterparties", queries.get_counterparties, _counterparty_args),
)
