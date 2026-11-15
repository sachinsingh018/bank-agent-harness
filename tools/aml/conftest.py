"""Local-only database fixtures for the tool tests. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Alerts for the tests live under their own ruleset version and are removed again.
The real v1 alerts are never touched."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, func, select

from harness.db.models import Alert
from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.rules.types import AlertDraft
from modules.aml.store import delete_alerts_for_version, write_alerts
from tests_support.aml_db import local_url, pick_samples
from tools.aml.db import build_tool_engine
from tools.aml.query_registry import Samples

TOOLS_VERSION = "test-tools"


@pytest.fixture(scope="session")
def local_engine() -> Iterator[Engine]:
    engine = create_engine(local_url(), echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def tool_engine(local_engine: Engine) -> Iterator[Engine]:
    """The guarded tool engine, built from the TEST url."""
    engine = build_tool_engine(local_url())
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True)
class ToolData:
    samples: Samples
    drafts: list[AlertDraft]


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
