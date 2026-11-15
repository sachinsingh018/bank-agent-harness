"""Read-only lookups the AML verifiers use to check findings against stored data.

Everything goes through the guarded door of the AML tools (build_tool_engine and open_readonly
in tools/aml/db.py) and the existing query functions, so there is no new SQL here and every
connection is a read-only transaction that is always rolled back. The answer key is never read.

The runner passes the database URL explicitly: DATABASE_URL in normal runs, TEST_DATABASE_URL
in tests. This module never reads the environment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import ValidationError
from sqlalchemy import Engine

from tools.aml.caps import MAX_ROWS
from tools.aml.db import build_tool_engine, open_readonly
from tools.aml.models import AlertIdInput, AlertTransactionsInput, TransactionInput
from tools.aml.paging import ToolFailure
from tools.aml.queries import get_alert, get_alert_transactions, get_transaction

Side = Literal["paid", "received"]


class IncompleteAlertError(Exception):
    """Paging an alert's transfers did not collect the full set. The message is generic."""


@dataclass(frozen=True)
class TransferView:
    """The stored amounts of one transfer, as exact decimals with their currencies."""

    txn_id: int
    paid: Decimal
    paid_currency: str
    received: Decimal
    received_currency: str

    def amount(self, side: Side) -> tuple[Decimal, str]:
        if side == "paid":
            return self.paid, self.paid_currency
        return self.received, self.received_currency


class AmlLookupLike(Protocol):
    """What the AML verifiers need. AmlLookup fits; tests use an in-memory fake."""

    def transfers(self, txn_ids: Iterable[int]) -> dict[int, TransferView]: ...

    def alert_txn_ids(self, alert_id: str) -> frozenset[int] | None: ...


class AmlLookup:
    """Read-only lookups on one guarded engine. Transfer results are cached per instance."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._found: dict[int, TransferView] = {}
        self._missing: set[int] = set()

    def transfers(self, txn_ids: Iterable[int]) -> dict[int, TransferView]:
        """Stored transfers by id. Ids that do not exist are absent from the result."""
        wanted = sorted(set(txn_ids))
        todo = [i for i in wanted if i not in self._found and i not in self._missing]
        if todo:
            with open_readonly(self._engine) as conn:
                for txn_id in todo:
                    try:
                        record = get_transaction(conn, TransactionInput(txn_id=txn_id))
                    except ToolFailure as failure:
                        if failure.code != "not_found":
                            raise
                        self._missing.add(txn_id)
                        continue
                    self._found[txn_id] = TransferView(
                        txn_id=record.txn_id,
                        paid=Decimal(record.amount_paid.value),
                        paid_currency=record.amount_paid.currency,
                        received=Decimal(record.amount_received.value),
                        received_currency=record.amount_received.currency,
                    )
        return {i: self._found[i] for i in wanted if i in self._found}

    def alert_txn_ids(self, alert_id: str) -> frozenset[int] | None:
        """Every transfer id of the alert, paging until the end. None if the alert does not
        exist. Raises IncompleteAlertError if paging does not collect the full set."""
        try:
            first = AlertIdInput(alert_id=alert_id)
        except ValidationError:
            return None
        with open_readonly(self._engine) as conn:
            try:
                alert = get_alert(conn, first)
            except ToolFailure as failure:
                if failure.code == "not_found":
                    return None
                raise
            page = alert.transfers
            ids = {t.txn_id for t in page.items}
            total = page.total_count
            cursor = page.next_cursor
            while cursor is not None:
                inp = AlertTransactionsInput(alert_id=alert_id, cursor=cursor, limit=MAX_ROWS)
                page = get_alert_transactions(conn, inp)
                if not page.items:
                    break
                ids.update(t.txn_id for t in page.items)
                cursor = page.next_cursor
        if len(ids) != total:
            raise IncompleteAlertError("alert transfer set is incomplete")
        return frozenset(ids)


class AmlLookupFactory:
    """Builds AmlLookup objects that share ONE guarded engine. Call close() when done."""

    def __init__(self, database_url: str) -> None:
        self._engine = build_tool_engine(database_url)

    def __call__(self) -> AmlLookup:
        return AmlLookup(self._engine)

    def close(self) -> None:
        self._engine.dispose()


def make_aml_lookup_factory(database_url: str) -> AmlLookupFactory:
    """The ModuleSpec hook: database url -> lookup factory (callable, with close())."""
    return AmlLookupFactory(database_url)
