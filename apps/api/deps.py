"""Settings and database connections for the API, as FastAPI dependencies.

Two connections, never mixed:
- records (HARNESS_WRITE_DATABASE_URL): reads runs and audit events, and appends human
  decisions through harness.records. This is the human write path, outside the agent.
- tool door (DATABASE_URL): reads transfers through the guarded read-only engine of the AML
  tools, so the API can never reach the answer key.

Settings are read from the environment when a request needs them. URLs and tokens are never
logged or echoed. Tests override get_records_engine and get_tool_engine with the local test
database, so the shell DATABASE_URL is never used by tests.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from harness.db.session import DatabaseConnectionError, get_database_url, is_local_url
from harness.records import get_write_engine
from tools.aml.db import build_tool_engine

REVIEWER_TOKEN_ENV = "REVIEWER_TOKEN"
REVIEWER_NAME_ENV = "REVIEWER_NAME"
# Set to "1" on a deployed server whose records database is not local.
ALLOW_REMOTE_ENV = "API_ALLOW_REMOTE_DATABASE"
REVIEWER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._\- ]{1,40}$")
NOT_CONFIGURED = "database is not configured"


class ReviewerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str | None
    name: str | None


def get_reviewer_settings() -> ReviewerSettings:
    """Read at request time, so a change of env needs no code path of its own."""
    return ReviewerSettings(
        token=os.environ.get(REVIEWER_TOKEN_ENV) or None,
        name=os.environ.get(REVIEWER_NAME_ENV) or None,
    )


def valid_reviewer_name(name: str) -> bool:
    return REVIEWER_NAME_PATTERN.fullmatch(name) is not None and name.strip() != ""


def _remote_allowed() -> bool:
    return os.environ.get(ALLOW_REMOTE_ENV) == "1"


@lru_cache(maxsize=1)
def _records_engine() -> Engine:
    return get_write_engine(allow_remote=_remote_allowed())


@lru_cache(maxsize=1)
def _tool_engine() -> Engine:
    url = get_database_url()
    if not is_local_url(url) and not _remote_allowed():
        raise RuntimeError(f"DATABASE_URL is not local; set {ALLOW_REMOTE_ENV}=1 to use it")
    return build_tool_engine(url)


def get_records_engine() -> Engine:
    try:
        return _records_engine()
    except (RuntimeError, DatabaseConnectionError):
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED) from None


def get_tool_engine() -> Engine:
    try:
        return _tool_engine()
    except (RuntimeError, DatabaseConnectionError):
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED) from None
