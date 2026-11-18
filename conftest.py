"""Repo-wide test setup: nothing is ever sent to Langfuse from the test suite.

The Codespace has the LANGFUSE_* env vars set, so they are removed for every test. A test that
needs them sets them itself and replaces the Langfuse client with a fake."""

from collections.abc import Iterator

import pytest

LANGFUSE_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL")


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in LANGFUSE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield
