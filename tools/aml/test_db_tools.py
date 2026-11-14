"""DB-backed tool tests. Local Postgres only (see conftest). Never reads DATABASE_URL.

The fixture commits alerts under its own ruleset version and removes them again."""

import re
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, insert, select
from sqlalchemy.exc import DBAPIError

from harness.db.models import Alert, Transaction
from tools.aml.caps import DEFAULT_ROWS, MAX_COUNTERPARTIES, MAX_RESPONSE_CHARS, MAX_ROWS
from tools.aml.conftest import ToolData
from tools.aml.db import (
    SetTransactionReadOnly,
    StatementRecorder,
    ToolSecurityError,
    open_readonly,
)
from tools.aml.models import (
    AccountHistoryInput,
    AccountInput,
    AlertIdInput,
    AlertTransactionsInput,
    CounterpartiesInput,
    TransactionInput,
)
from tools.aml.paging import ToolFailure
from tools.aml.queries import (
    get_account,
    get_account_history,
    get_alert,
    get_alert_transactions,
    get_counterparties,
    get_transaction,
)
from tools.aml.query_registry import QUERY_REGISTRY

# Test files are outside the static scan. The name is built from two pieces so the full
# literal never appears in non-test code, which keeps the repo-wide scan auditable.
LABELS = "transaction" + "_labels"
FORBIDDEN_KEYS = {"is_laundering", "laundering", "attempt_id", "attempt_type"}


def _keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(str(k))
            found |= _keys(v)
    elif isinstance(value, list):
        for v in value:
            found |= _keys(v)
    return found


def test_every_query_runs_and_is_safe(tool_engine: Engine, tool_data: ToolData) -> None:
    recorder = StatementRecorder()
    recorder.install(tool_engine)
    calls = 0
    with open_readonly(tool_engine) as conn:
        for entry in QUERY_REGISTRY:
            for args in entry.build_args(tool_data.samples):
                result = entry.call(conn, args)
                dumped = result.model_dump(mode="json")
                calls += 1
                assert not FORBIDDEN_KEYS & _keys(dumped), entry.name
                assert len(result.model_dump_json()) <= MAX_RESPONSE_CHARS, entry.name
    assert calls >= 10
    assert recorder.statements
    for sql in recorder.statements:
        assert LABELS not in sql.lower()


def test_get_alert(tool_engine: Engine, tool_data: ToolData) -> None:
    s = tool_data.samples
    with open_readonly(tool_engine) as conn:
        alert = get_alert(conn, AlertIdInput(alert_id=s.alert_id))
    assert alert.alert_id == s.alert_id
    assert alert.ruleset_version == "test-tools"
    page = alert.transfers
    assert page.total_count == alert.txn_count
    assert page.returned == len(page.items) == min(25, alert.txn_count)
    assert page.truncated == (alert.txn_count > 25)
    assert (page.next_cursor is not None) == page.truncated
    assert all(t.role for t in page.items)
    keys = [(t.ts, t.txn_id) for t in page.items]
    assert keys == sorted(keys)


def test_get_alert_transactions_paginates_without_repeats(
    tool_engine: Engine, tool_data: ToolData
) -> None:
    s = tool_data.samples
    with open_readonly(tool_engine) as conn:
        total = get_alert(conn, AlertIdInput(alert_id=s.alert_id)).txn_count
        seen: list[int] = []
        cursor: str | None = None
        for _ in range(total + 2):
            page = get_alert_transactions(
                conn, AlertTransactionsInput(alert_id=s.alert_id, cursor=cursor, limit=7)
            )
            assert page.total_count == total
            assert page.returned == len(page.items) <= 7
            seen.extend(t.txn_id for t in page.items)
            cursor = page.next_cursor
            if cursor is None:
                assert not page.truncated
                break
            assert page.truncated
        else:
            pytest.fail("pagination did not end")
    assert len(seen) == total
    assert len(set(seen)) == total


def test_get_transaction(tool_engine: Engine, tool_data: ToolData) -> None:
    with open_readonly(tool_engine) as conn:
        rec = get_transaction(conn, TransactionInput(txn_id=tool_data.samples.txn_id))
        row = conn.execute(
            select(Transaction.amt_paid, Transaction.cur_paid, Transaction.cur_recv).where(
                Transaction.txn_id == rec.txn_id
            )
        ).one()
    assert rec.amount_paid.currency == row.cur_paid
    assert rec.amount_received.currency == row.cur_recv
    assert rec.amount_paid.value == format(row.amt_paid, "f")
    assert re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", rec.amount_paid.value)


def test_get_account(tool_engine: Engine, tool_data: ToolData) -> None:
    a = tool_data.samples.account
    with open_readonly(tool_engine) as conn:
        summary = get_account(conn, AccountInput(bank=a.bank, acct=a.acct))
        history = get_account_history(
            conn, AccountHistoryInput(bank=a.bank, acct=a.acct, limit=MAX_ROWS)
        )
    assert summary.account == a
    assert summary.first_seen is not None and summary.last_seen is not None
    assert summary.first_seen <= summary.last_seen
    total = summary.transfers_in + summary.transfers_out + summary.self_transfers
    assert history.total_count == total
    assert summary.currencies == sorted(set(summary.currencies))
    assert summary.formats == sorted(set(summary.formats))
    assert summary.busiest_day is not None and 1 <= summary.busiest_day.transfers <= total


def test_history_pagination_and_directions(tool_engine: Engine, tool_data: ToolData) -> None:
    a = tool_data.samples.account
    with open_readonly(tool_engine) as conn:
        both_total = get_account_history(
            conn, AccountHistoryInput(bank=a.bank, acct=a.acct)
        ).total_count
        seen: list[int] = []
        cursor: str | None = None
        for _ in range(both_total + 2):
            page = get_account_history(
                conn, AccountHistoryInput(bank=a.bank, acct=a.acct, cursor=cursor, limit=4)
            )
            seen.extend(t.txn_id for t in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        outgoing = get_account_history(
            conn, AccountHistoryInput(bank=a.bank, acct=a.acct, direction="out", limit=MAX_ROWS)
        )
        incoming = get_account_history(
            conn, AccountHistoryInput(bank=a.bank, acct=a.acct, direction="in", limit=MAX_ROWS)
        )
        default = get_account_history(conn, AccountHistoryInput(bank=a.bank, acct=a.acct))
    assert len(seen) == both_total == len(set(seen))
    assert default.returned <= DEFAULT_ROWS
    assert all(t.from_account == a for t in outgoing.items)
    assert all(t.to_account == a for t in incoming.items)


def test_history_window(tool_engine: Engine, tool_data: ToolData) -> None:
    s = tool_data.samples
    a = s.account
    with open_readonly(tool_engine) as conn:
        page = get_account_history(
            conn,
            AccountHistoryInput(bank=a.bank, acct=a.acct, start=s.start, end=s.end, limit=MAX_ROWS),
        )
    assert page.returned > 0
    assert all(s.start <= t.ts <= s.end for t in page.items)


def test_get_counterparties(tool_engine: Engine, tool_data: ToolData) -> None:
    a = tool_data.samples.account
    with open_readonly(tool_engine) as conn:
        result = get_counterparties(conn, CounterpartiesInput(bank=a.bank, acct=a.acct))
        outgoing = get_counterparties(
            conn, CounterpartiesInput(bank=a.bank, acct=a.acct, direction="out")
        )
    assert result.returned == len(result.items) <= MAX_COUNTERPARTIES
    assert result.total_count >= result.returned
    assert result.truncated == (result.total_count > result.returned)
    counts = [c.transfers for c in result.items]
    assert counts == sorted(counts, reverse=True)
    for c in result.items:
        assert c.transfers == c.sent_transfers + c.received_transfers
        assert c.first_seen <= c.last_seen
        assert c.account != a
        for totals in (c.sent_totals_by_currency, c.received_totals_by_currency):
            currencies = [m.currency for m in totals]
            assert currencies == sorted(set(currencies))
    assert all(c.received_transfers == 0 for c in outgoing.items)


def test_not_found_and_bad_cursor(tool_engine: Engine, tool_data: ToolData) -> None:
    s = tool_data.samples
    with open_readonly(tool_engine) as conn:
        for call in (
            lambda: get_alert(conn, AlertIdInput(alert_id="AL-000000000000")),
            lambda: get_alert_transactions(
                conn, AlertTransactionsInput(alert_id="AL-000000000000")
            ),
            lambda: get_transaction(conn, TransactionInput(txn_id=2_000_000_000)),
            lambda: get_account(conn, AccountInput(bank=987654, acct="NOPE")),
            lambda: get_account_history(conn, AccountHistoryInput(bank=987654, acct="NOPE")),
            lambda: get_counterparties(conn, CounterpartiesInput(bank=987654, acct="NOPE")),
        ):
            with pytest.raises(ToolFailure) as info:
                call()
            assert info.value.code == "not_found"
        with pytest.raises(ToolFailure) as info:
            get_alert_transactions(
                conn, AlertTransactionsInput(alert_id=s.alert_id, cursor="garbage!")
            )
        assert info.value.code == "invalid_argument"


def test_guard_refuses_the_answer_key_table(tool_engine: Engine, tool_data: ToolData) -> None:
    recorder = StatementRecorder()
    recorder.install(tool_engine)
    with open_readonly(tool_engine) as conn:
        with pytest.raises(ToolSecurityError) as info:
            conn.exec_driver_sql(f"select count(*) from {LABELS}")
        assert LABELS not in str(info.value)
        with pytest.raises(ToolSecurityError):
            conn.exec_driver_sql(f"select 1 where exists (select 1 from {LABELS.upper()})")
        assert conn.execute(select(func.count()).select_from(Alert)).scalar_one() > 0
    assert not any(LABELS in sql.lower() for sql in recorder.statements)


def test_insert_through_the_tool_door_fails(tool_engine: Engine, tool_data: ToolData) -> None:
    values = {"alert_id": "AL-ffffffffffff"}
    with open_readonly(tool_engine) as conn:
        with pytest.raises(ToolSecurityError):
            conn.execute(insert(Alert).values(values))


def test_database_itself_is_read_only(local_engine: Engine, tool_data: ToolData) -> None:
    """Layer 1 on its own: with no guard, the read-only transaction still blocks writes."""
    import os

    plain = create_engine(os.environ["TEST_DATABASE_URL"], echo=False, hide_parameters=True)
    try:
        with plain.connect() as conn:
            conn.begin()
            conn.execute(SetTransactionReadOnly())
            with pytest.raises(DBAPIError):
                conn.execute(
                    insert(Alert).values(
                        alert_id="AL-eeeeeeeeeeee",
                        ruleset_version="test-tools-ro",
                        detector="cycle",
                        window_start=tool_data.samples.start,
                        window_end=tool_data.samples.end,
                        txn_count=0,
                        account_count=0,
                        params={},
                        summary="x",
                    )
                )
            conn.rollback()
    finally:
        plain.dispose()
    with local_engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(Alert).where(Alert.alert_id == "AL-eeeeeeeeeeee")
            ).scalar_one()
            == 0
        )
