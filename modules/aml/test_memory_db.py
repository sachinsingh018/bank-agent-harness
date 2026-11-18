"""The AML lessons provider on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only. Test
alerts live under their own ruleset version and are removed again; corrections and runs are
removed by tests_support.records_db."""

import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import Engine, insert

from harness.db.models import Alert
from modules.aml.memory import AmlLessonsProvider
from modules.aml.store import delete_alerts_for_version
from tests_support.aml_db import local_url
from tests_support.lessons_db import save_correction
from tests_support.records_db import records_cleaned
from tools.aml.db import build_tool_engine

VERSION = "test-memory"


@pytest.fixture()
def alerts(local_engine: Engine) -> Iterator[dict[str, str]]:
    """Four test alerts: now and same are fan-out, other is a cycle, heldout is fan-out."""
    tag = uuid.uuid4().hex[:8]
    ids = {k: f"AL-mem-{k}-{tag}" for k in ("now", "same", "other", "heldout")}
    detector = {"now": "fan-out", "same": "fan-out", "other": "cycle", "heldout": "fan-out"}
    t = datetime(2022, 9, 1)
    delete_alerts_for_version(local_engine, VERSION)
    with local_engine.begin() as conn:
        for key, alert_id in ids.items():
            conn.execute(
                insert(Alert).values(
                    alert_id=alert_id,
                    ruleset_version=VERSION,
                    detector=detector[key],
                    window_start=t,
                    window_end=t,
                    txn_count=1,
                    account_count=1,
                    params={},
                    summary="test",
                )
            )
    try:
        yield ids
    finally:
        delete_alerts_for_version(local_engine, VERSION)


def test_provider_reads_corrections_and_picks_by_detector(
    local_engine: Engine, alerts: dict[str, str]
) -> None:
    data_engine = build_tool_engine(local_url())
    try:

        def save(case_id: str, rule: str) -> int:
            return save_correction(local_engine, case_id, rule)

        with records_cleaned(local_engine):
            on_now = save(alerts["now"], "A lesson saved on this very alert.")
            other = save(alerts["other"], "A lesson from a cycle alert.")
            same = save(alerts["same"], "A lesson from a fan-out alert.")
            held = save(alerts["heldout"], "A lesson from a held-out alert.")
            newer_other = save(alerts["other"], "A newer cycle lesson.")
            provider = AmlLessonsProvider(local_engine, data_engine, frozenset({alerts["heldout"]}))
            lessons = provider(alerts["now"])
            ids = [lesson.id for lesson in lessons]
            assert on_now not in ids and held not in ids
            assert ids[:3] == [same, newer_other, other]
            assert lessons[0].text == "A lesson from a fan-out alert."
            assert provider.sources[same] == alerts["same"]
            assert provider.sources[other] == alerts["other"]
            assert on_now not in provider.sources
    finally:
        data_engine.dispose()
