"""Edge cases on rows the test inserts itself.

One write connection, one transaction, always rolled back, so nothing is ever committed. The
inserts use high txn_ids and their own bank number so they cannot clash with real rows. The guard
is installed on the connection after the inserts, so the tools run under the same rules as in
production. Local Postgres only (see conftest)."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, func, insert, select

from harness.db.models import Account, Transaction
from tools.aml.db import install_guard
from tools.aml.models import (
    AccountHistoryInput,
    AccountInput,
    AccountRef,
    CounterpartiesInput,
    TransferRecord,
)
from tools.aml.paging import encode_cursor
from tools.aml.queries import get_account, get_account_history, get_counterparties

BANK = 990001
T0 = datetime(2031, 1, 1, 12, 0, 0)
FIRST_ID = 900001


def _ref(name: str) -> AccountRef:
    return AccountRef(bank=BANK, acct=name)


class Ledger:
    """Inserts test transactions with fresh high ids."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.next_id = FIRST_ID

    def add(
        self,
        src: str,
        dst: str,
        ts: datetime,
        paid: str = "10.50",
        cur_paid: str = "USD",
        recv: str | None = None,
        cur_recv: str | None = None,
    ) -> int:
        txn_id = self.next_id
        self.next_id += 1
        self.conn.execute(
            insert(Transaction).values(
                txn_id=txn_id,
                ts=ts,
                from_bank=BANK,
                from_acct=src,
                to_bank=BANK,
                to_acct=dst,
                amt_paid=Decimal(paid),
                cur_paid=cur_paid,
                amt_recv=Decimal(recv or paid),
                cur_recv=cur_recv or cur_paid,
                fmt="ACH",
                row_hash=f"edge-case-{txn_id}",
            )
        )
        return txn_id


@pytest.fixture
def ledger(local_engine: Engine) -> Iterator[Ledger]:
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            names = ["TIE", "SINK", "SELF", "PEER", "SRC", "DST", "BIG", "OTHER"]
            conn.execute(insert(Account), [{"bank": BANK, "acct": n} for n in names])
            yield Ledger(conn)
        finally:
            trans.rollback()


def _ids(items: list[TransferRecord]) -> list[int]:
    return [t.txn_id for t in items]


@pytest.mark.parametrize("limit", [1, 2])
def test_identical_timestamps_page_without_skips_or_repeats(ledger: Ledger, limit: int) -> None:
    expected = [ledger.add("TIE", "SINK", T0) for _ in range(5)]
    expected.append(ledger.add("TIE", "SINK", T0 + timedelta(seconds=1)))
    install_guard(ledger.conn)
    seen: list[int] = []
    cursor: str | None = None
    for _ in range(len(expected) + 2):
        page = get_account_history(
            ledger.conn, AccountHistoryInput(bank=BANK, acct="TIE", cursor=cursor, limit=limit)
        )
        assert page.total_count == len(expected)
        seen.extend(_ids(page.items))
        cursor = page.next_cursor
        if cursor is None:
            break
    else:
        pytest.fail("pagination did not end")
    assert seen == expected


def test_self_transfers_are_counted_apart_and_not_counterparties(ledger: Ledger) -> None:
    ledger.add("SELF", "SELF", T0)
    ledger.add("SELF", "SELF", T0 + timedelta(minutes=1))
    ledger.add("SELF", "PEER", T0 + timedelta(minutes=2))
    ledger.add("PEER", "SELF", T0 + timedelta(minutes=3))
    ledger.add("PEER", "SELF", T0 + timedelta(minutes=4))
    install_guard(ledger.conn)
    summary = get_account(ledger.conn, AccountInput(bank=BANK, acct="SELF"))
    assert summary.self_transfers == 2
    assert summary.transfers_out == 1
    assert summary.transfers_in == 2
    assert summary.distinct_counterparties_in == 1
    assert summary.distinct_counterparties_out == 1
    result = get_counterparties(ledger.conn, CounterpartiesInput(bank=BANK, acct="SELF"))
    assert [c.account for c in result.items] == [_ref("PEER")]
    assert result.total_count == 1
    peer = result.items[0]
    assert (peer.sent_transfers, peer.received_transfers) == (1, 2)


def test_multi_currency_totals_are_per_currency(ledger: Ledger) -> None:
    for i, (paid, cur_paid, recv, cur_recv) in enumerate(
        [
            ("100.10", "USD", "92.00", "EUR"),
            ("200.20", "USD", "184.00", "EUR"),
            ("50.00", "EUR", "50.00", "EUR"),
            ("7.25", "GBP", "9.00", "USD"),
        ]
    ):
        ledger.add("SRC", "DST", T0 + timedelta(minutes=i), paid, cur_paid, recv, cur_recv)
    install_guard(ledger.conn)
    conn = ledger.conn

    def plain_sums(amount: object, currency: object, where: object) -> dict[str, Decimal]:
        rows = conn.execute(
            select(currency, func.sum(amount)).where(where).group_by(currency)  # type: ignore[call-overload]
        ).all()
        return {cur: total for cur, total in rows}

    sent_expected = plain_sums(
        Transaction.amt_paid, Transaction.cur_paid, Transaction.from_acct == "SRC"
    )
    recv_expected = plain_sums(
        Transaction.amt_recv, Transaction.cur_recv, Transaction.from_acct == "SRC"
    )
    assert set(sent_expected) == {"USD", "EUR", "GBP"}
    result = get_counterparties(conn, CounterpartiesInput(bank=BANK, acct="SRC", direction="out"))
    assert len(result.items) == 1
    sent = {m.currency: Decimal(m.value) for m in result.items[0].sent_totals_by_currency}
    assert sent == sent_expected
    assert sent["USD"] == Decimal("300.30")
    back = get_counterparties(conn, CounterpartiesInput(bank=BANK, acct="DST", direction="in"))
    received = {m.currency: Decimal(m.value) for m in back.items[0].received_totals_by_currency}
    assert received == recv_expected
    assert received["EUR"] == Decimal("326.00")
    assert len(back.items[0].received_totals_by_currency) == 2


def test_character_cap_truncates_a_real_page_and_paging_loses_nothing(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = [ledger.add("BIG", "OTHER", T0 + timedelta(seconds=i)) for i in range(12)]
    install_guard(ledger.conn)
    monkeypatch.setattr("tools.aml.paging.MAX_RESPONSE_CHARS", 1500)
    args = AccountHistoryInput(bank=BANK, acct="BIG", limit=12)
    first = get_account_history(ledger.conn, args)
    assert first.truncated
    assert 0 < first.returned < 12
    assert first.total_count == 12
    last = first.items[-1]
    assert first.next_cursor == encode_cursor(last.ts, last.txn_id)
    seen = _ids(first.items)
    cursor: str | None = first.next_cursor
    for _ in range(20):
        page = get_account_history(
            ledger.conn, AccountHistoryInput(bank=BANK, acct="BIG", cursor=cursor, limit=12)
        )
        seen.extend(_ids(page.items))
        cursor = page.next_cursor
        if cursor is None:
            assert not page.truncated
            break
    else:
        pytest.fail("pagination did not end")
    assert seen == expected
