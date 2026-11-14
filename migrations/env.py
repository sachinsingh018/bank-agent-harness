"""Alembic environment. The URL is read only through harness.db.session.get_database_url."""

from alembic import context
from sqlalchemy import create_engine, pool

from harness.db.models import Base
from harness.db.session import DatabaseConnectionError, get_database_url

config = context.config
if config.config_file_name is not None:
    from logging.config import fileConfig

    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_database_url()
    try:
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
    except Exception:
        raise DatabaseConnectionError("Invalid database URL (details hidden)") from None
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    try:
        engine = create_engine(url, poolclass=pool.NullPool, echo=False, hide_parameters=True)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    except DatabaseConnectionError:
        raise
    except Exception:
        raise DatabaseConnectionError("Migration failed (details hidden)") from None


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
