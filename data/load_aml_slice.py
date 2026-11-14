"""Load the small AML slice CSV into Postgres, and verify the result.

Usage (from the repo root):
    uv run python -m data.load_aml_slice load [--dry-run] [--csv PATH]
    uv run python -m data.load_aml_slice verify [--csv PATH]

The database URL comes from the environment only and is never printed. Every driver error is
replaced by a generic message (see harness.db.session).
"""

import argparse
import csv
import hashlib
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url

from harness.db.models import ATTEMPT_TYPES, Account, Transaction, TransactionLabel
from harness.db.session import (
    DatabaseConnectionError,
    get_database_url,
    get_engine,
    run_safely,
)

DEFAULT_CSV = Path(__file__).parent / "aml_slice_hi_small.csv"

CSV_COLUMNS: tuple[str, ...] = (
    "ts",
    "from_bank",
    "from_acct",
    "to_bank",
    "to_acct",
    "amt_recv",
    "cur_recv",
    "amt_paid",
    "cur_paid",
    "fmt",
    "laundering",
    "attempt_id",
    "attempt_type",
)
# Hashed source columns. The label columns are left out on purpose: transactions is visible to
# the agent, and a hash that covered the label could be used to guess it.
HASH_COLUMNS: tuple[str, ...] = CSV_COLUMNS[:10]
UNIT_SEP = "\x1f"
TS_FORMAT = "%Y/%m/%d %H:%M"
BATCH_SIZE = 1000
CONNECT_ATTEMPTS = 3

# Expected numbers for verify.
EXPECTED_ROWS = 25_493
EXPECTED_LAUNDERING = 522
EXPECTED_NORMAL = 24_971
EXPECTED_SCHEMES_PER_FAMILY = 8
EXPECTED_SCHEMES_TOTAL = 64
EXPECTED_FAMILY_TRANSFERS: dict[str, int] = {
    "BIPARTITE": 41,
    "CYCLE": 47,
    "FAN-IN": 59,
    "FAN-OUT": 54,
    "GATHER-SCATTER": 116,
    "RANDOM": 27,
    "SCATTER-GATHER": 114,
    "STACK": 64,
}
EXPECTED_MIN_TS = datetime(2022, 9, 1, 0, 0)
EXPECTED_MAX_TS = datetime(2022, 9, 17, 19, 40)


class LoaderError(Exception):
    """A clear, safe message for the operator. Never contains the database URL."""


@dataclass(frozen=True)
class Row:
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
    row_hash: str
    is_laundering: bool
    attempt_id: int | None
    attempt_type: str | None


@dataclass(frozen=True)
class Summary:
    rows: int
    laundering: int
    normal: int
    accounts: int


# ----------------------------------------------------------------------------------------------
# Pure functions (no database)
# ----------------------------------------------------------------------------------------------


def compute_row_hash(raw: dict[str, str]) -> str:
    joined = UNIT_SEP.join(raw[c] for c in HASH_COLUMNS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _fail(line: int, message: str) -> LoaderError:
    return LoaderError(f"CSV row {line}: {message}")


def _parse_int(line: int, name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise _fail(line, f"{name} is not a valid integer") from None


def _parse_decimal(line: int, name: str, value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise _fail(line, f"{name} is not a valid decimal") from None
    if not number.is_finite():
        raise _fail(line, f"{name} is not a finite decimal")
    return number


def _parse_attempt_id(line: int, value: str) -> int | None:
    if value == "":
        return None
    number = _parse_decimal(line, "attempt_id", value)
    if number != number.to_integral_value() or number < 0:
        raise _fail(line, "attempt_id is not a whole number")
    return int(number)


def parse_row(position: int, raw: dict[str, str]) -> Row:
    """Parse one CSV record. position is the 1-based row position in the file (the txn_id)."""
    line = position
    for name in CSV_COLUMNS[:11]:
        if raw[name].strip() == "":
            raise _fail(line, f"required field {name} is blank")
    try:
        ts = datetime.strptime(raw["ts"], TS_FORMAT)
    except ValueError:
        raise _fail(line, "ts is not a valid timestamp") from None
    flag = raw["laundering"]
    if flag not in ("0", "1"):
        raise _fail(line, "laundering must be 0 or 1")
    is_laundering = flag == "1"
    attempt_id = _parse_attempt_id(line, raw["attempt_id"])
    attempt_type = raw["attempt_type"] or None
    if attempt_type is not None and attempt_type not in ATTEMPT_TYPES:
        raise _fail(line, "attempt_type is not one of the 8 known families")
    if is_laundering and (attempt_id is None or attempt_type is None):
        raise _fail(line, "laundering row must have both attempt_id and attempt_type")
    if not is_laundering and (attempt_id is not None or attempt_type is not None):
        raise _fail(line, "normal row must have neither attempt_id nor attempt_type")
    return Row(
        txn_id=position,
        ts=ts,
        from_bank=_parse_int(line, "from_bank", raw["from_bank"]),
        from_acct=raw["from_acct"],
        to_bank=_parse_int(line, "to_bank", raw["to_bank"]),
        to_acct=raw["to_acct"],
        amt_recv=_parse_decimal(line, "amt_recv", raw["amt_recv"]),
        cur_recv=raw["cur_recv"],
        amt_paid=_parse_decimal(line, "amt_paid", raw["amt_paid"]),
        cur_paid=raw["cur_paid"],
        fmt=raw["fmt"],
        row_hash=compute_row_hash(raw),
        is_laundering=is_laundering,
        attempt_id=attempt_id,
        attempt_type=attempt_type,
    )


def read_rows(path: Path) -> list[Row]:
    """Parse and validate the whole file. Raises LoaderError on any problem."""
    try:
        handle = path.open(newline="", encoding="utf-8")
    except OSError:
        raise LoaderError("cannot open the CSV file") from None
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != CSV_COLUMNS:
            raise LoaderError("CSV header does not match the 13 expected columns")
        rows: list[Row] = []
        seen: dict[str, int] = {}
        for position, raw in enumerate(reader, start=1):
            if None in raw or any(v is None for v in raw.values()):
                raise _fail(position, "wrong number of fields")
            row = parse_row(position, {k: v for k, v in raw.items() if isinstance(v, str)})
            if row.row_hash in seen:
                raise _fail(position, f"duplicate of row {seen[row.row_hash]}")
            seen[row.row_hash] = position
            rows.append(row)
    if not rows:
        raise LoaderError("CSV file has no data rows")
    return rows


def distinct_accounts(rows: Sequence[Row]) -> list[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for r in rows:
        found.add((r.from_bank, r.from_acct))
        found.add((r.to_bank, r.to_acct))
    return sorted(found)


def summarize(rows: Sequence[Row]) -> Summary:
    laundering = sum(1 for r in rows if r.is_laundering)
    return Summary(
        rows=len(rows),
        laundering=laundering,
        normal=len(rows) - laundering,
        accounts=len(distinct_accounts(rows)),
    )


def _batches[T](items: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), BATCH_SIZE):
        yield items[start : start + BATCH_SIZE]


def target_label(url: str) -> str:
    """Only a coarse label. The URL itself is never shown."""
    try:
        host = make_url(url).host
    except Exception:
        host = None
    return "local database" if host in ("db", "localhost", "127.0.0.1") else "remote database"


# ----------------------------------------------------------------------------------------------
# Database work (all wrapped in run_safely)
# ----------------------------------------------------------------------------------------------


def _connect_with_retry(engine: Engine) -> None:
    """Wake the database (Neon wakes slowly). Generic messages only."""
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            run_safely(lambda: engine.connect().close())
            return
        except DatabaseConnectionError:
            if attempt == CONNECT_ATTEMPTS:
                raise DatabaseConnectionError(
                    f"Could not connect after {CONNECT_ATTEMPTS} attempts (details hidden)"
                ) from None
            print(f"connect attempt {attempt} failed, retrying")
            time.sleep(2 * attempt)


def _insert_batches(conn: Connection, table: Any, records: Sequence[dict[str, Any]]) -> int:
    inserted = 0
    for batch in _batches(records):
        stmt = insert(table).values(list(batch)).on_conflict_do_nothing()
        # RETURNING gives an exact count of new rows (rowcount is unreliable here).
        inserted += len(conn.execute(stmt.returning(literal_column("1"))).all())
    return inserted


def _load_in_transaction(engine: Engine, rows: Sequence[Row]) -> dict[str, int]:
    with engine.begin() as conn:
        existing = {
            int(t): h for t, h in conn.execute(select(Transaction.txn_id, Transaction.row_hash))
        }
        for r in rows:
            old = existing.get(r.txn_id)
            if old is not None and old != r.row_hash:
                raise LoaderError("file changed since last load, refusing to mix versions")
        accounts = [{"bank": b, "acct": a} for b, a in distinct_accounts(rows)]
        txns = [
            {
                "txn_id": r.txn_id,
                "ts": r.ts,
                "from_bank": r.from_bank,
                "from_acct": r.from_acct,
                "to_bank": r.to_bank,
                "to_acct": r.to_acct,
                "amt_recv": r.amt_recv,
                "amt_paid": r.amt_paid,
                "cur_recv": r.cur_recv,
                "cur_paid": r.cur_paid,
                "fmt": r.fmt,
                "row_hash": r.row_hash,
            }
            for r in rows
        ]
        labels = [
            {
                "txn_id": r.txn_id,
                "is_laundering": r.is_laundering,
                "attempt_id": r.attempt_id,
                "attempt_type": r.attempt_type,
            }
            for r in rows
        ]
        return {
            "accounts": _insert_batches(conn, Account, accounts),
            "transactions": _insert_batches(conn, Transaction, txns),
            "transaction_labels": _insert_batches(conn, TransactionLabel, labels),
        }


def run_load(rows: Sequence[Row]) -> dict[str, int]:
    url = get_database_url()
    print(f"target: {target_label(url)}")
    engine = get_engine()
    try:
        _connect_with_retry(engine)
        return run_safely(lambda: _load_in_transaction(engine, rows))
    finally:
        engine.dispose()


def _verify_queries(conn: Connection, rows: Sequence[Row]) -> list[str]:
    problems: list[str] = []

    def check(name: str, actual: object, expected: object) -> None:
        if actual != expected:
            problems.append(f"{name}: expected {expected}, found {actual}")

    n_txn = conn.execute(select(func.count()).select_from(Transaction)).scalar_one()
    n_lab = conn.execute(select(func.count()).select_from(TransactionLabel)).scalar_one()
    check("transactions rows", n_txn, EXPECTED_ROWS)
    check("transaction_labels rows", n_lab, EXPECTED_ROWS)

    n_launder = conn.execute(
        select(func.count()).select_from(TransactionLabel).where(TransactionLabel.is_laundering)
    ).scalar_one()
    n_normal = conn.execute(
        select(func.count())
        .select_from(TransactionLabel)
        .where(TransactionLabel.is_laundering.is_(False))
    ).scalar_one()
    check("laundering rows", n_launder, EXPECTED_LAUNDERING)
    check("normal rows", n_normal, EXPECTED_NORMAL)
    check("laundering rows vs CSV", n_launder, sum(1 for r in rows if r.is_laundering))

    fam = conn.execute(
        select(
            TransactionLabel.attempt_type,
            func.count(),
            func.count(func.distinct(TransactionLabel.attempt_id)),
        )
        .where(TransactionLabel.attempt_type.is_not(None))
        .group_by(TransactionLabel.attempt_type)
    ).all()
    transfers = {str(t): int(c) for t, c, _ in fam}
    schemes = {str(t): int(s) for t, _, s in fam}
    check("transfers per family", dict(sorted(transfers.items())), EXPECTED_FAMILY_TRANSFERS)
    check(
        "schemes per family",
        dict(sorted(schemes.items())),
        {f: EXPECTED_SCHEMES_PER_FAMILY for f in sorted(EXPECTED_FAMILY_TRANSFERS)},
    )
    check("total schemes", sum(schemes.values()), EXPECTED_SCHEMES_TOTAL)

    min_ts, max_ts = conn.execute(select(func.min(Transaction.ts), func.max(Transaction.ts))).one()
    check("min ts", min_ts, EXPECTED_MIN_TS)
    check("max ts", max_ts, EXPECTED_MAX_TS)

    paid, recv = conn.execute(
        select(func.sum(Transaction.amt_paid), func.sum(Transaction.amt_recv))
    ).one()
    check("sum amt_paid", paid, sum((r.amt_paid for r in rows), Decimal(0)))
    check("sum amt_recv", recv, sum((r.amt_recv for r in rows), Decimal(0)))

    orphan_labels = conn.execute(
        select(func.count())
        .select_from(TransactionLabel)
        .outerjoin(Transaction, Transaction.txn_id == TransactionLabel.txn_id)
        .where(Transaction.txn_id.is_(None))
    ).scalar_one()
    orphan_txns = conn.execute(
        select(func.count())
        .select_from(Transaction)
        .outerjoin(TransactionLabel, Transaction.txn_id == TransactionLabel.txn_id)
        .where(TransactionLabel.txn_id.is_(None))
    ).scalar_one()
    check("labels without a transaction", orphan_labels, 0)
    check("transactions without a label", orphan_txns, 0)
    from_a = Account.__table__.alias("from_a")
    to_a = Account.__table__.alias("to_a")
    no_account = conn.execute(
        select(func.count())
        .select_from(Transaction)
        .outerjoin(
            from_a,
            (from_a.c.bank == Transaction.from_bank) & (from_a.c.acct == Transaction.from_acct),
        )
        .outerjoin(
            to_a, (to_a.c.bank == Transaction.to_bank) & (to_a.c.acct == Transaction.to_acct)
        )
        .where(from_a.c.bank.is_(None) | to_a.c.bank.is_(None))
    ).scalar_one()
    check("transactions without accounts", no_account, 0)
    return problems


def run_verify(rows: Sequence[Row]) -> list[str]:
    get_database_url()
    engine = get_engine()
    try:
        _connect_with_retry(engine)

        def work() -> list[str]:
            with engine.connect() as conn:
                return _verify_queries(conn, rows)

        return run_safely(work)
    finally:
        engine.dispose()


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m data.load_aml_slice")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="validate, then load into the database")
    load.add_argument("--dry-run", action="store_true", help="validate only, no connection")
    load.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    verify = sub.add_parser("verify", help="read-only check of the loaded data")
    verify.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        rows = read_rows(args.csv)
        summary = summarize(rows)
        if args.command == "load":
            if args.dry_run:
                print(
                    f"dry run OK: rows={summary.rows} laundering={summary.laundering} "
                    f"normal={summary.normal} accounts={summary.accounts}"
                )
                return 0
            inserted = run_load(rows)
            for table, count in inserted.items():
                print(f"inserted into {table}: {count}")
            return 0
        problems = run_verify(rows)
        if problems:
            print("VERIFY FAILED")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("VERIFY OK")
        return 0
    except (LoaderError, DatabaseConnectionError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
