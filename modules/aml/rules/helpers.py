"""Small builders for hand-made transfers in tests."""

from datetime import datetime, timedelta
from decimal import Decimal

from modules.aml.rules.types import Account, Txn

T0 = datetime(2022, 9, 1, 0, 0)


def acct(name: str) -> Account:
    return (1, name)


def txn(i: int, hours: float, src: str, dst: str) -> Txn:
    return Txn(
        txn_id=i,
        ts=T0 + timedelta(hours=hours),
        from_bank=1,
        from_acct=src,
        to_bank=1,
        to_acct=dst,
        amt_recv=Decimal("10"),
        cur_recv="USD",
        amt_paid=Decimal("10"),
        cur_paid="USD",
        fmt="ACH",
    )
