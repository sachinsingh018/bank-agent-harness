"""The only writer for the rules. Touches alerts and alert_transactions, nothing else."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, Engine, delete, literal_column
from sqlalchemy.dialects.postgresql import insert

from harness.db.models import Alert, AlertTransaction
from harness.db.session import run_safely
from modules.aml.rules.ids import make_alert_id
from modules.aml.rules.types import AlertDraft

BATCH_SIZE = 1000


def _insert_count(conn: Connection, table: Any, records: Sequence[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(records), BATCH_SIZE):
        stmt = insert(table).values(list(records[start : start + BATCH_SIZE]))
        stmt = stmt.on_conflict_do_nothing()
        inserted += len(conn.execute(stmt.returning(literal_column("1"))).all())
    return inserted


def write_alerts_on(
    conn: Connection, drafts: Sequence[AlertDraft], ruleset_version: str
) -> dict[str, int]:
    alerts: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for d in drafts:
        alert_id = make_alert_id(ruleset_version, d.detector, d.txn_ids)
        alerts.append(
            {
                "alert_id": alert_id,
                "ruleset_version": ruleset_version,
                "detector": d.detector,
                "anchor_bank": d.anchor[0] if d.anchor else None,
                "anchor_acct": d.anchor[1] if d.anchor else None,
                "window_start": d.window_start,
                "window_end": d.window_end,
                "txn_count": len(d.txn_ids),
                "account_count": d.account_count,
                "params": dict(d.params),
                "summary": d.summary,
            }
        )
        links.extend({"alert_id": alert_id, "txn_id": t, "role": d.roles[t]} for t in d.txn_ids)
    return {
        "alerts": _insert_count(conn, Alert, alerts),
        "alert_transactions": _insert_count(conn, AlertTransaction, links),
    }


def write_alerts(
    engine: Engine, drafts: Sequence[AlertDraft], ruleset_version: str
) -> dict[str, int]:
    def work() -> dict[str, int]:
        with engine.begin() as conn:
            return write_alerts_on(conn, drafts, ruleset_version)

    return run_safely(work)


def delete_alerts_for_version_on(conn: Connection, ruleset_version: str) -> int:
    result = conn.execute(delete(Alert).where(Alert.ruleset_version == ruleset_version))
    return result.rowcount


def delete_alerts_for_version(engine: Engine, ruleset_version: str) -> int:
    def work() -> int:
        with engine.begin() as conn:
            return delete_alerts_for_version_on(conn, ruleset_version)

    return run_safely(work)
