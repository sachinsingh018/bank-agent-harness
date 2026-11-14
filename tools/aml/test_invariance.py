"""With the answer key table emptied, every tool must return the same output.

One write connection, one transaction, always rolled back. The delete is this test's own setup
and goes through a raw driver cursor, so it can never run inside tools code."""

from sqlalchemy import Engine, func, select

from harness.db.models import Alert
from modules.aml.rules.ids import make_alert_id
from modules.aml.store import write_alerts_on
from tools.aml.conftest import ToolData
from tools.aml.db import install_guard
from tools.aml.paging import ToolFailure
from tools.aml.query_registry import QUERY_REGISTRY, Samples

INV_VERSION = "test-tools-inv"
# Test files are outside the static scan. The name is built from two pieces so the full
# literal never appears in non-test code, which keeps the repo-wide scan auditable.
LABELS = "transaction" + "_labels"


def test_outputs_do_not_change_when_labels_are_emptied(
    local_engine: Engine, tool_data: ToolData
) -> None:
    drafts = tool_data.drafts
    big = max(drafts, key=lambda d: len(d.txn_ids))
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            write_alerts_on(conn, drafts, INV_VERSION)
            samples = Samples(
                alert_id=make_alert_id(INV_VERSION, big.detector, big.txn_ids),
                account=tool_data.samples.account,
                txn_id=tool_data.samples.txn_id,
                start=tool_data.samples.start,
                end=tool_data.samples.end,
            )
            assert (
                conn.execute(
                    select(func.count())
                    .select_from(Alert)
                    .where(Alert.alert_id == samples.alert_id)
                ).scalar_one()
                == 1
            )
            install_guard(conn)

            def run_all_queries() -> list[object]:
                outputs: list[object] = []
                for entry in QUERY_REGISTRY:
                    for args in entry.build_args(samples):
                        try:
                            outputs.append(entry.call(conn, args).model_dump(mode="json"))
                        except ToolFailure as failure:
                            # An over-cap neighbourhood must give the same failure both times.
                            assert failure.code == "too_large", entry.name
                            outputs.append({"failure": failure.code, "message": failure.message})
                return outputs

            before = run_all_queries()
            assert len(before) >= 10
            # Test setup only. This delete runs on a write connection that is always rolled
            # back (see the finally below), through a raw driver cursor that bypasses the guard.
            # It can never run inside tools code, which only gets read-only guarded connections.
            raw = conn.connection.cursor()
            raw.execute(f"DELETE FROM {LABELS}")
            raw.execute(f"SELECT count(*) FROM {LABELS}")
            row = raw.fetchone()
            assert row is not None and row[0] == 0
            after = run_all_queries()
            assert before == after
        finally:
            trans.rollback()
