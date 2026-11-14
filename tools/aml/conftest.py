"""Local-only database fixtures for the tool tests. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Alerts for the tests live under their own ruleset version and are removed again.
The real v1 alerts are never touched."""

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url

from harness.db.models import Alert, AlertTransaction, Transaction
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.rules.types import AlertDraft
from modules.aml.store import delete_alerts_for_version, write_alerts
from tools.aml.db import build_tool_engine
from tools.aml.models import AccountRef
from tools.aml.query_registry import Samples

LOCAL_HOSTS = ("db", "localhost", "127.0.0.1")
TOOLS_VERSION = "test-tools"


def _local_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        host = make_url(url).host
    except Exception:
        pytest.skip("TEST_DATABASE_URL is not valid")
    if host not in LOCAL_HOSTS:
        pytest.skip("TEST_DATABASE_URL is not a local host")
    return url


@pytest.fixture(scope="session")
def local_engine() -> Iterator[Engine]:
    engine = create_engine(_local_url(), echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def tool_engine(local_engine: Engine) -> Iterator[Engine]:
    """The guarded tool engine, built from the TEST url."""
    engine = build_tool_engine(_local_url())
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True)
class ToolData:
    samples: Samples
    drafts: list[AlertDraft]


def pick_samples(engine: Engine, version: str) -> Samples:
    """Take sample arguments from real data: the biggest alert of this version."""
    with engine.connect() as conn:
        alert = conn.execute(
            select(
                Alert.alert_id,
                Alert.anchor_bank,
                Alert.anchor_acct,
                Alert.window_start,
                Alert.window_end,
            )
            .where(Alert.ruleset_version == version)
            .order_by(Alert.txn_count.desc(), Alert.alert_id)
            .limit(1)
        ).one()
        first = conn.execute(
            select(
                Transaction.txn_id,
                Transaction.from_bank,
                Transaction.from_acct,
            )
            .join(AlertTransaction, AlertTransaction.txn_id == Transaction.txn_id)
            .where(AlertTransaction.alert_id == alert.alert_id)
            .order_by(Transaction.ts, Transaction.txn_id)
            .limit(1)
        ).one()
    if alert.anchor_bank is not None:
        account = AccountRef(bank=alert.anchor_bank, acct=alert.anchor_acct)
    else:
        account = AccountRef(bank=first.from_bank, acct=first.from_acct)
    return Samples(
        alert_id=alert.alert_id,
        account=account,
        txn_id=first.txn_id,
        start=alert.window_start,
        end=alert.window_end,
    )


# Module scoped on purpose: finding the alerts runs every detector over the slice, which is slow,
# so each test module builds them once and shares them. They are read only by the tests.
@pytest.fixture(scope="module")
def tool_data(local_engine: Engine) -> Iterator[ToolData]:
    # Clear any stale leftover of this version (for example from a crashed run) before starting.
    delete_alerts_for_version(local_engine, TOOLS_VERSION)
    try:
        drafts = run_all(load_txns(local_engine))
        assert drafts, "the rules found no alerts in the test database"
        write_alerts(local_engine, drafts, TOOLS_VERSION)
        with local_engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(Alert)).scalar_one() > 0
        yield ToolData(samples=pick_samples(local_engine, TOOLS_VERSION), drafts=drafts)
    finally:
        delete_alerts_for_version(local_engine, TOOLS_VERSION)
