"""DB-backed checks. Local Postgres only (see conftest). Every transaction is rolled back."""

from sqlalchemy import Engine, delete, func, select

from harness.db.models import Alert, AlertTransaction, TransactionLabel
from modules.aml.load_txns import load_txns_on
from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.rules.ids import make_alert_id
from modules.aml.rules.types import AlertDraft
from modules.aml.store import delete_alerts_for_version_on, write_alerts_on


def _ids(drafts: list[AlertDraft]) -> list[str]:
    return [make_alert_id("v1", d.detector, d.txn_ids) for d in drafts]


def test_alert_ids_do_not_depend_on_labels(local_engine: Engine) -> None:
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            txns = load_txns_on(conn)
            assert len(txns) > 0
            before = _ids(fan_out(txns)) + _ids(fan_in(txns))
            assert before
            conn.execute(delete(TransactionLabel))
            assert conn.execute(select(func.count()).select_from(TransactionLabel)).scalar() == 0
            after_txns = load_txns_on(conn)
            after = _ids(fan_out(after_txns)) + _ids(fan_in(after_txns))
            assert before == after
        finally:
            trans.rollback()


def test_write_alerts_is_rerunnable(local_engine: Engine) -> None:
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            txns = load_txns_on(conn)
            drafts = fan_out(txns) + fan_in(txns)
            assert drafts
            delete_alerts_for_version_on(conn, "v1")
            first = write_alerts_on(conn, drafts, "v1")
            assert first["alerts"] == len(drafts)
            assert first["alert_transactions"] == sum(len(d.txn_ids) for d in drafts)
            assert write_alerts_on(conn, drafts, "v1") == {"alerts": 0, "alert_transactions": 0}
            assert delete_alerts_for_version_on(conn, "v1") == len(drafts)
            left = conn.execute(select(func.count()).select_from(AlertTransaction)).scalar()
            assert left == 0
            assert conn.execute(select(func.count()).select_from(Alert)).scalar() == 0
        finally:
            trans.rollback()
