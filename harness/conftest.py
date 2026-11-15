"""Local-only database fixtures for the harness tests. Reads TEST_DATABASE_URL only, never
DATABASE_URL. Alerts for the tests live under their own ruleset version and are removed again."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine

from modules.aml.load_txns import load_txns
from modules.aml.rules.registry import run_all
from modules.aml.store import delete_alerts_for_version, write_alerts
from tests_support.aml_db import local_url, pick_samples
from tools.aml.query_registry import Samples

HARNESS_VERSION = "test-harness-mcp"


@pytest.fixture(scope="session")
def harness_test_url() -> str:
    return local_url()


@pytest.fixture(scope="session")
def harness_engine(harness_test_url: str) -> Iterator[Engine]:
    engine = create_engine(harness_test_url, echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def harness_samples(harness_engine: Engine) -> Iterator[Samples]:
    """Real alerts from the rules, written under a test ruleset version and removed after."""
    delete_alerts_for_version(harness_engine, HARNESS_VERSION)
    try:
        drafts = run_all(load_txns(harness_engine))
        assert drafts, "the rules found no alerts in the test database"
        write_alerts(harness_engine, drafts, HARNESS_VERSION)
        yield pick_samples(harness_engine, HARNESS_VERSION)
    finally:
        delete_alerts_for_version(harness_engine, HARNESS_VERSION)
