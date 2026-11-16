"""Database helpers. The URL comes from the environment only and is never shown."""

import os
from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

REQUIRED_PREFIX = "postgresql+psycopg://"
LOCAL_HOSTS = ("db", "localhost", "127.0.0.1")
REDIRECT_QUERY_KEYS = ("host", "hostaddr", "service")


class DatabaseConnectionError(RuntimeError):
    """Generic connection failure. Never carries the original driver message."""


def get_url_from(env_var: str) -> str:
    """Read a database URL from one env var. Error messages name the var, never the value."""
    url = os.environ.get(env_var)
    if not url:
        raise RuntimeError(f"{env_var} is not set")
    if not url.startswith(REQUIRED_PREFIX):
        raise RuntimeError(f"{env_var} must use the postgresql+psycopg:// prefix")
    return url


def get_database_url() -> str:
    return get_url_from("DATABASE_URL")


def engine_for_url(url: str) -> Engine:
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


def get_engine_for(env_var: str) -> Engine:
    return engine_for_url(get_url_from(env_var))


def get_engine() -> Engine:
    return get_engine_for("DATABASE_URL")


def is_local_url(url: str) -> bool:
    """True only for a single plain local host. A missing host (socket or env defaults), a
    host list, or a query that can redirect the connection (host, hostaddr, service) counts
    as not local."""
    try:
        parsed = make_url(url)
    except Exception:
        return False
    host = parsed.host
    if not host or "," in host:
        return False
    if any(key in parsed.query for key in REDIRECT_QUERY_KEYS):
        return False
    return host in LOCAL_HOSTS


def run_safely[T](fn: Callable[[], T]) -> T:
    """Run a database action. Any driver error becomes a generic one, with no cause attached."""
    try:
        return fn()
    except (SQLAlchemyError, OSError):
        raise DatabaseConnectionError("Database operation failed (details hidden)") from None
