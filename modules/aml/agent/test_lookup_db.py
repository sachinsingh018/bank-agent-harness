"""AmlLookup against the local test database, through the guarded read-only door.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only."""

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select

from harness.db.models import AlertTransaction, Transaction
from modules.aml.agent.lookup import AmlLookup, make_aml_lookup_factory
from tools.aml.caps import DEFAULT_ROWS
from tools.aml.db import StatementRecorder, build_tool_engine, open_readonly
from tools.aml.models import TransactionInput
from tools.aml.queries import get_transaction
from tools.aml.query_registry import Samples


@pytest.fixture()
def tool_engine(harness_test_url: str) -> Iterator[Engine]:
    engine = build_tool_engine(harness_test_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _alert_ids(engine: Engine, alert_id: str) -> set[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(AlertTransaction.txn_id).where(AlertTransaction.alert_id == alert_id)
        ).all()
    return {r.txn_id for r in rows}


def test_alert_txn_ids_pages_through_a_large_alert(
    harness_test_url: str, harness_engine: Engine, harness_samples: Samples
) -> None:
    expected = _alert_ids(harness_engine, harness_samples.alert_id)
    if len(expected) <= DEFAULT_ROWS:
        pytest.skip(f"biggest test alert has {len(expected)} transfers, paging needs more")
    factory = make_aml_lookup_factory(harness_test_url)
    try:
        assert factory().alert_txn_ids(harness_samples.alert_id) == frozenset(expected)
    finally:
        factory.close()


def test_alert_txn_ids_unknown_alert_is_none(tool_engine: Engine) -> None:
    lookup = AmlLookup(tool_engine)
    assert lookup.alert_txn_ids("AL-000000000000") is None
    assert lookup.alert_txn_ids("not-an-alert") is None


def test_transfers_match_get_transaction_and_are_cached(
    tool_engine: Engine, harness_engine: Engine, harness_samples: Samples
) -> None:
    ids = sorted(_alert_ids(harness_engine, harness_samples.alert_id))[:5]
    with harness_engine.connect() as conn:
        missing = int(conn.execute(select(func.max(Transaction.txn_id))).scalar_one()) + 1000
    recorder = StatementRecorder()
    recorder.install(tool_engine)
    lookup = AmlLookup(tool_engine)
    views = lookup.transfers([*ids, missing])
    assert sorted(views) == ids
    with open_readonly(tool_engine) as conn:
        for txn_id in ids:
            record = get_transaction(conn, TransactionInput(txn_id=txn_id))
            view = views[txn_id]
            assert view.txn_id == txn_id
            assert view.paid == Decimal(record.amount_paid.value)
            assert view.paid_currency == record.amount_paid.currency
            assert view.received == Decimal(record.amount_received.value)
            assert view.received_currency == record.amount_received.currency
    before = len(recorder.statements)
    assert lookup.transfers([*ids, missing]) == views
    assert len(recorder.statements) == before  # cached, missing ids included
