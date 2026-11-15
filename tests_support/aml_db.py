"""Shared helpers for the local database tests. Reads TEST_DATABASE_URL only, never
DATABASE_URL, and skips the calling test unless it points at a local host."""

import os

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.engine import make_url

from harness.db.models import Alert, AlertTransaction, Transaction
from tools.aml.models import AccountRef
from tools.aml.query_registry import Samples

LOCAL_HOSTS = ("db", "localhost", "127.0.0.1")


def local_url() -> str:
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
