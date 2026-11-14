import pytest

from harness.db.models import Base
from harness.db.session import (
    DatabaseConnectionError,
    get_database_url,
    get_engine,
    run_safely,
)

SECRET = "s3cr3t-value-xyz"


def test_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        get_database_url()


def test_wrong_prefix_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{SECRET}@host/db")
    with pytest.raises(RuntimeError) as exc:
        get_database_url()
    assert SECRET not in str(exc.value)
    assert "host" not in str(exc.value)


def test_valid_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"postgresql+psycopg://user:{SECRET}@host/db"
    monkeypatch.setenv("DATABASE_URL", url)
    assert get_database_url() == url


def test_run_safely_hides_cause() -> None:
    from sqlalchemy.exc import OperationalError

    def boom() -> None:
        raise OperationalError("stmt", None, Exception(f"host {SECRET}"))

    with pytest.raises(DatabaseConnectionError) as exc:
        run_safely(boom)
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


def test_check_constraints_in_metadata() -> None:
    table = Base.metadata.tables["transaction_labels"]
    sql = " ".join(str(c.sqltext) for c in table.constraints if hasattr(c, "sqltext"))
    for family in ("BIPARTITE", "FAN-IN", "SCATTER-GATHER", "STACK"):
        assert f"'{family}'" in sql
    assert "NOT is_laundering AND attempt_id IS NULL AND attempt_type IS NULL" in sql
    assert "is_laundering AND attempt_id IS NOT NULL AND attempt_type IS NOT NULL" in sql


def test_malformed_url_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://user:{SECRET}@host:notaport/db")
    with pytest.raises(DatabaseConnectionError) as exc:
        get_engine()
    assert SECRET not in str(exc.value)
    assert "host" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__
