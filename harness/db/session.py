"""Database helpers. The URL comes from the environment only and is never shown."""

import os
from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

REQUIRED_PREFIX = "postgresql+psycopg://"


class DatabaseConnectionError(RuntimeError):
    """Generic connection failure. Never carries the original driver message."""


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    if not url.startswith(REQUIRED_PREFIX):
        raise RuntimeError("DATABASE_URL must use the postgresql+psycopg:// prefix")
    return url


def get_engine() -> Engine:
    url = get_database_url()
    try:
        return create_engine(
            url,
            echo=False,
            hide_parameters=True,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
        )
    except Exception:
        # A malformed URL makes SQLAlchemy echo the whole string, password included.
        raise DatabaseConnectionError("Invalid database URL (details hidden)") from None


def run_safely[T](fn: Callable[[], T]) -> T:
    """Run a database action. Any driver error becomes a generic one, with no cause attached."""
    try:
        return fn()
    except (SQLAlchemyError, OSError):
        raise DatabaseConnectionError("Database operation failed (details hidden)") from None
