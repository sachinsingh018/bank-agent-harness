"""The only database reader for the rules. Reads named columns of transactions, nothing else."""

from sqlalchemy import Connection, Engine, select

from harness.db.models import Transaction
from harness.db.session import run_safely
from modules.aml.rules.types import Txn

_COLUMNS = (
    Transaction.txn_id,
    Transaction.ts,
    Transaction.from_bank,
    Transaction.from_acct,
    Transaction.to_bank,
    Transaction.to_acct,
    Transaction.amt_recv,
    Transaction.cur_recv,
    Transaction.amt_paid,
    Transaction.cur_paid,
    Transaction.fmt,
)


def load_txns_on(conn: Connection) -> list[Txn]:
    rows = conn.execute(select(*_COLUMNS).order_by(Transaction.txn_id)).all()
    return [
        Txn(
            txn_id=r.txn_id,
            ts=r.ts,
            from_bank=r.from_bank,
            from_acct=r.from_acct,
            to_bank=r.to_bank,
            to_acct=r.to_acct,
            amt_recv=r.amt_recv,
            cur_recv=r.cur_recv,
            amt_paid=r.amt_paid,
            cur_paid=r.cur_paid,
            fmt=r.fmt,
        )
        for r in rows
    ]


def load_txns(engine: Engine) -> list[Txn]:
    def work() -> list[Txn]:
        with engine.connect() as conn:
            return load_txns_on(conn)

    return run_safely(work)
