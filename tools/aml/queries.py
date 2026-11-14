"""Read-only query functions. Each takes a connection and validated input and returns models.

Only the Transaction, Account, Alert and AlertTransaction models are used, through SQLAlchemy
expressions. Ordering is always time then txn_id and paging is keyset based.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Date,
    Row,
    Select,
    and_,
    cast,
    func,
    or_,
    select,
    tuple_,
)

from harness.db.models import Account, Alert, AlertTransaction, Transaction
from tools.aml.caps import DEFAULT_ROWS, MAX_COUNTERPARTIES
from tools.aml.models import (
    AccountHistoryInput,
    AccountInput,
    AccountRef,
    AccountSummary,
    AlertIdInput,
    AlertRecord,
    AlertTransactionsInput,
    AlertTransfer,
    AlertTransferPage,
    BusiestDay,
    CounterpartiesInput,
    CounterpartiesResult,
    CounterpartyRecord,
    Money,
    TransactionInput,
    TransferPage,
    TransferRecord,
)
from tools.aml.paging import (
    ToolFailure,
    decode_cursor,
    encode_cursor,
    fit_items,
    json_size,
    make_page,
)

AccountKey = tuple[int, str]


def _transfer_select() -> Select[*tuple[Any, ...]]:
    return select(
        Transaction.txn_id,
        Transaction.ts,
        Transaction.from_bank,
        Transaction.from_acct,
        Transaction.to_bank,
        Transaction.to_acct,
        Transaction.amt_paid,
        Transaction.cur_paid,
        Transaction.amt_recv,
        Transaction.cur_recv,
        Transaction.fmt,
    )


def _transfer_fields(row: Row[Any]) -> dict[str, Any]:
    return {
        "txn_id": row.txn_id,
        "ts": row.ts,
        "from_account": AccountRef(bank=row.from_bank, acct=row.from_acct),
        "to_account": AccountRef(bank=row.to_bank, acct=row.to_acct),
        "amount_paid": Money.of(row.amt_paid, row.cur_paid),
        "amount_received": Money.of(row.amt_recv, row.cur_recv),
        "format": row.fmt,
    }


def _record(row: Row[Any]) -> TransferRecord:
    return TransferRecord(**_transfer_fields(row))


def _alert_transfer(row: Row[Any]) -> AlertTransfer:
    return AlertTransfer(**_transfer_fields(row), role=row.role)


def _is_sender(bank: int, acct: str) -> ColumnElement[bool]:
    return and_(Transaction.from_bank == bank, Transaction.from_acct == acct)


def _is_receiver(bank: int, acct: str) -> ColumnElement[bool]:
    return and_(Transaction.to_bank == bank, Transaction.to_acct == acct)


def _window(start: datetime | None, end: datetime | None) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if start is not None:
        conds.append(Transaction.ts >= start)
    if end is not None:
        conds.append(Transaction.ts <= end)
    return conds


def _after(cursor: str | None) -> list[ColumnElement[bool]]:
    if cursor is None:
        return []
    ts, txn_id = decode_cursor(cursor)
    return [tuple_(Transaction.ts, Transaction.txn_id) > tuple_(ts, txn_id)]


def _cursor_of(item: TransferRecord) -> str:
    return encode_cursor(item.ts, item.txn_id)


def _require_account(conn: Connection, bank: int, acct: str) -> None:
    found = conn.execute(
        select(Account.bank).where(Account.bank == bank, Account.acct == acct)
    ).first()
    if found is None:
        raise ToolFailure("not_found", "account not found")


def _require_alert(conn: Connection, alert_id: str) -> None:
    found = conn.execute(select(Alert.alert_id).where(Alert.alert_id == alert_id)).first()
    if found is None:
        raise ToolFailure("not_found", "alert not found")


def _alert_page(
    conn: Connection, alert_id: str, cursor: str | None, limit: int, reserved: int
) -> AlertTransferPage:
    total = conn.execute(
        select(func.count())
        .select_from(AlertTransaction)
        .where(AlertTransaction.alert_id == alert_id)
    ).scalar_one()
    stmt = (
        _transfer_select()
        .add_columns(AlertTransaction.role)
        .join(AlertTransaction, AlertTransaction.txn_id == Transaction.txn_id)
        .where(AlertTransaction.alert_id == alert_id, *_after(cursor))
        .order_by(Transaction.ts, Transaction.txn_id)
        .limit(limit + 1)
    )
    rows = conn.execute(stmt).all()
    items = [_alert_transfer(r) for r in rows[:limit]]
    return make_page(
        AlertTransferPage,
        items,
        total_count=total,
        has_more=len(rows) > limit,
        cursor_of=_cursor_of,
        reserved=reserved,
    )


def get_alert(conn: Connection, inp: AlertIdInput) -> AlertRecord:
    row = conn.execute(
        select(
            Alert.alert_id,
            Alert.ruleset_version,
            Alert.detector,
            Alert.anchor_bank,
            Alert.anchor_acct,
            Alert.window_start,
            Alert.window_end,
            Alert.txn_count,
            Alert.account_count,
            Alert.params,
            Alert.summary,
        ).where(Alert.alert_id == inp.alert_id)
    ).one_or_none()
    if row is None:
        raise ToolFailure("not_found", "alert not found")
    anchor = (
        AccountRef(bank=row.anchor_bank, acct=row.anchor_acct)
        if row.anchor_bank is not None and row.anchor_acct is not None
        else None
    )
    fields: dict[str, Any] = {
        "alert_id": row.alert_id,
        "ruleset_version": row.ruleset_version,
        "detector": row.detector,
        "anchor": anchor,
        "window_start": row.window_start,
        "window_end": row.window_end,
        "txn_count": row.txn_count,
        "account_count": row.account_count,
        "params": dict(row.params),
        "summary": row.summary,
    }
    empty = AlertTransferPage(items=[], total_count=0, returned=0, truncated=True)
    reserved = json_size(AlertRecord(**fields, transfers=empty))
    page = _alert_page(conn, row.alert_id, None, DEFAULT_ROWS, reserved)
    return AlertRecord(**fields, transfers=page)


def get_alert_transactions(conn: Connection, inp: AlertTransactionsInput) -> AlertTransferPage:
    _require_alert(conn, inp.alert_id)
    return _alert_page(conn, inp.alert_id, inp.cursor, inp.limit, 0)


def get_transaction(conn: Connection, inp: TransactionInput) -> TransferRecord:
    row = conn.execute(_transfer_select().where(Transaction.txn_id == inp.txn_id)).one_or_none()
    if row is None:
        raise ToolFailure("not_found", "transaction not found")
    return _record(row)


def _involved(bank: int, acct: str) -> ColumnElement[bool]:
    return or_(_is_sender(bank, acct), _is_receiver(bank, acct))


def get_account(conn: Connection, inp: AccountInput) -> AccountSummary:
    bank, acct = inp.bank, inp.acct
    _require_account(conn, bank, acct)
    is_self = and_(_is_sender(bank, acct), _is_receiver(bank, acct))
    totals = conn.execute(
        select(
            func.min(Transaction.ts).label("first_seen"),
            func.max(Transaction.ts).label("last_seen"),
            func.count().filter(and_(_is_receiver(bank, acct), ~is_self)).label("n_in"),
            func.count().filter(and_(_is_sender(bank, acct), ~is_self)).label("n_out"),
            func.count().filter(is_self).label("n_self"),
        ).where(_involved(bank, acct))
    ).one()
    senders = (
        select(Transaction.from_bank, Transaction.from_acct)
        .where(_is_receiver(bank, acct), ~is_self)
        .distinct()
        .subquery()
    )
    receivers = (
        select(Transaction.to_bank, Transaction.to_acct)
        .where(_is_sender(bank, acct), ~is_self)
        .distinct()
        .subquery()
    )
    n_senders = conn.execute(select(func.count()).select_from(senders)).scalar_one()
    n_receivers = conn.execute(select(func.count()).select_from(receivers)).scalar_one()
    paid_cur = conn.execute(
        select(Transaction.cur_paid).where(_is_sender(bank, acct)).distinct()
    ).scalars()
    recv_cur = conn.execute(
        select(Transaction.cur_recv).where(_is_receiver(bank, acct)).distinct()
    ).scalars()
    currencies = sorted({*paid_cur, *recv_cur})
    formats = sorted(
        conn.execute(select(Transaction.fmt).where(_involved(bank, acct)).distinct()).scalars()
    )
    day = cast(Transaction.ts, Date).label("day")
    busiest_row = conn.execute(
        select(day, func.count().label("n"))
        .where(_involved(bank, acct))
        .group_by(day)
        .order_by(func.count().desc(), day)
        .limit(1)
    ).one_or_none()
    busiest = None
    if busiest_row is not None:
        busiest_day: date = busiest_row.day
        busiest = BusiestDay(day=busiest_day, transfers=busiest_row.n)
    return AccountSummary(
        account=AccountRef(bank=bank, acct=acct),
        first_seen=totals.first_seen,
        last_seen=totals.last_seen,
        transfers_in=totals.n_in,
        transfers_out=totals.n_out,
        self_transfers=totals.n_self,
        distinct_counterparties_in=n_senders,
        distinct_counterparties_out=n_receivers,
        currencies=currencies,
        formats=formats,
        busiest_day=busiest,
    )


def get_account_history(conn: Connection, inp: AccountHistoryInput) -> TransferPage:
    bank, acct = inp.bank, inp.acct
    _require_account(conn, bank, acct)
    if inp.direction == "out":
        side = _is_sender(bank, acct)
    elif inp.direction == "in":
        side = _is_receiver(bank, acct)
    else:
        side = _involved(bank, acct)
    base = [side, *_window(inp.start, inp.end)]
    total = conn.execute(select(func.count()).select_from(Transaction).where(*base)).scalar_one()
    stmt = (
        _transfer_select()
        .where(*base, *_after(inp.cursor))
        .order_by(Transaction.ts, Transaction.txn_id)
        .limit(inp.limit + 1)
    )
    rows = conn.execute(stmt).all()
    items = [_record(r) for r in rows[: inp.limit]]
    return make_page(
        TransferPage,
        items,
        total_count=total,
        has_more=len(rows) > inp.limit,
        cursor_of=_cursor_of,
    )


def _side_filter(
    bank: int, acct: str, outgoing: bool, start: datetime | None, end: datetime | None
) -> list[ColumnElement[bool]]:
    """Transfers between this account and some other account, in one direction."""
    if outgoing:
        other_differs = or_(Transaction.to_bank != bank, Transaction.to_acct != acct)
        return [_is_sender(bank, acct), other_differs, *_window(start, end)]
    other_differs = or_(Transaction.from_bank != bank, Transaction.from_acct != acct)
    return [_is_receiver(bank, acct), other_differs, *_window(start, end)]


def _side_counts(
    conn: Connection, inp: CounterpartiesInput, outgoing: bool
) -> dict[AccountKey, tuple[int, datetime, datetime]]:
    cp_bank = Transaction.to_bank if outgoing else Transaction.from_bank
    cp_acct = Transaction.to_acct if outgoing else Transaction.from_acct
    rows = conn.execute(
        select(
            cp_bank.label("bank"),
            cp_acct.label("acct"),
            func.count().label("n"),
            func.min(Transaction.ts).label("first_seen"),
            func.max(Transaction.ts).label("last_seen"),
        )
        .where(*_side_filter(inp.bank, inp.acct, outgoing, inp.start, inp.end))
        .group_by(cp_bank, cp_acct)
    ).all()
    return {(r.bank, r.acct): (r.n, r.first_seen, r.last_seen) for r in rows}


def _side_totals(
    conn: Connection, inp: CounterpartiesInput, outgoing: bool, keys: Sequence[AccountKey]
) -> dict[AccountKey, list[Money]]:
    if not keys:
        return {}
    cp_bank = Transaction.to_bank if outgoing else Transaction.from_bank
    cp_acct = Transaction.to_acct if outgoing else Transaction.from_acct
    amount = Transaction.amt_paid if outgoing else Transaction.amt_recv
    currency = Transaction.cur_paid if outgoing else Transaction.cur_recv
    rows = conn.execute(
        select(
            cp_bank.label("bank"),
            cp_acct.label("acct"),
            currency.label("currency"),
            func.sum(amount).label("total"),
        )
        .where(
            *_side_filter(inp.bank, inp.acct, outgoing, inp.start, inp.end),
            tuple_(cp_bank, cp_acct).in_(list(keys)),
        )
        .group_by(cp_bank, cp_acct, currency)
        .order_by(currency)
    ).all()
    out: dict[AccountKey, list[Money]] = defaultdict(list)
    for r in rows:
        total: Decimal = r.total
        out[(r.bank, r.acct)].append(Money.of(total, r.currency))
    return out


def get_counterparties(conn: Connection, inp: CounterpartiesInput) -> CounterpartiesResult:
    _require_account(conn, inp.bank, inp.acct)
    sent = _side_counts(conn, inp, True) if inp.direction in ("out", "both") else {}
    received = _side_counts(conn, inp, False) if inp.direction in ("in", "both") else {}
    keys = set(sent) | set(received)

    def n_transfers(key: AccountKey) -> int:
        return sent.get(key, (0,))[0] + received.get(key, (0,))[0]

    top = sorted(keys, key=lambda k: (-n_transfers(k), k))[:MAX_COUNTERPARTIES]
    sent_totals = _side_totals(conn, inp, True, top) if sent else {}
    received_totals = _side_totals(conn, inp, False, top) if received else {}
    items: list[CounterpartyRecord] = []
    for key in top:
        sides = [s[key] for s in (sent, received) if key in s]
        items.append(
            CounterpartyRecord(
                account=AccountRef(bank=key[0], acct=key[1]),
                transfers=n_transfers(key),
                first_seen=min(s[1] for s in sides),
                last_seen=max(s[2] for s in sides),
                sent_transfers=sent[key][0] if key in sent else 0,
                received_transfers=received[key][0] if key in received else 0,
                sent_totals_by_currency=sent_totals.get(key, []),
                received_totals_by_currency=received_totals.get(key, []),
            )
        )
    account = AccountRef(bank=inp.bank, acct=inp.acct)

    def measure(kept: list[CounterpartyRecord]) -> int:
        return json_size(
            CounterpartiesResult(
                account=account,
                items=kept,
                total_count=len(keys),
                returned=len(kept),
                truncated=True,
            )
        )

    kept = fit_items(items, measure)
    return CounterpartiesResult(
        account=account,
        items=kept,
        total_count=len(keys),
        returned=len(kept),
        truncated=len(kept) < len(keys),
    )
