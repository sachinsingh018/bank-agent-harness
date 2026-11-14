"""Plain in-memory records for the rules. No database code lives here."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Account = tuple[int, str]


@dataclass(frozen=True)
class Txn:
    txn_id: int
    ts: datetime
    from_bank: int
    from_acct: str
    to_bank: int
    to_acct: str
    amt_recv: Decimal
    cur_recv: str
    amt_paid: Decimal
    cur_paid: str
    fmt: str

    @property
    def sender(self) -> Account:
        return (self.from_bank, self.from_acct)

    @property
    def receiver(self) -> Account:
        return (self.to_bank, self.to_acct)


@dataclass(frozen=True)
class AlertDraft:
    detector: str
    anchor: Account | None
    window_start: datetime
    window_end: datetime
    txn_ids: tuple[int, ...]
    roles: Mapping[int, str]
    account_count: int
    params: Mapping[str, int | str | float]
    summary: str
