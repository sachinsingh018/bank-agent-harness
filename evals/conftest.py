"""Local-only database fixture. Reads TEST_DATABASE_URL only, never DATABASE_URL."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

LOCAL_HOSTS = ("db", "localhost", "127.0.0.1")


@pytest.fixture(scope="session")
def local_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        host = make_url(url).host
    except Exception:
        pytest.skip("TEST_DATABASE_URL is not valid")
    if host not in LOCAL_HOSTS:
        pytest.skip("TEST_DATABASE_URL is not a local host")
    engine = create_engine(url, echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()
