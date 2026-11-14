"""DB-backed checks for run_rules. Local Postgres only (see conftest)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select

from harness.db.models import Alert, AlertTransaction
from modules.aml.run_rules import run
from modules.aml.store import delete_alerts_for_version


def _count(engine: Engine, table: type[Alert] | type[AlertTransaction]) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


@pytest.fixture
def clean_v1(local_engine: Engine) -> Iterator[Engine]:
    delete_alerts_for_version(local_engine, "v1")
    try:
        yield local_engine
    finally:
        delete_alerts_for_version(local_engine, "v1")


def test_unknown_ruleset_refused(local_engine: Engine) -> None:
    with pytest.raises(ValueError):
        run(local_engine, "nope", dry_run=True, replace=False)


def test_dry_run_writes_nothing(clean_v1: Engine) -> None:
    summary = run(clean_v1, "v1", dry_run=True, replace=True)
    assert summary.total > 0
    assert summary.inserted_alerts == 0
    assert _count(clean_v1, Alert) == 0
    assert _count(clean_v1, AlertTransaction) == 0


def test_write_rerun_and_replace(clean_v1: Engine) -> None:
    first = run(clean_v1, "v1", dry_run=False, replace=False)
    assert first.inserted_alerts == first.total > 0
    assert _count(clean_v1, Alert) == first.total
    second = run(clean_v1, "v1", dry_run=False, replace=False)
    assert second.inserted_alerts == 0
    assert second.inserted_links == 0
    third = run(clean_v1, "v1", dry_run=False, replace=True)
    assert third.deleted == first.total
    assert third.inserted_alerts == first.total
    assert third.inserted_links == first.inserted_links
    assert _count(clean_v1, Alert) == first.total
