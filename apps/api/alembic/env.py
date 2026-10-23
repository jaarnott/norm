import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.db.models import Base
from app.db import mcp_models  # noqa: F401 — register MCP tables on Base.metadata
from app.db import functions_models  # noqa: F401 — Norm Functions (fn_*) tables
from app.db import payroll_models, entertainers_models, finance_models  # noqa: F401 — finance: pay_*/ent_*/fin_* tables

target_metadata = Base.metadata

# Override sqlalchemy.url from DATABASE_URL env var if set (for CI/CD and production)
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # A migration that can't get its lock gives up instead of queueing: while
    # DDL waits for a lock, every query on the tables it already holds waits
    # behind it. On 17 Oct 2026 a DROP TABLE waited half an hour behind a
    # transaction left idle in production and held the app's venue and thread
    # reads all that time. Past this the run rolls back (one transaction) and
    # the deploy stops with production on the old image — re-run it once the
    # database is quiet. Set at connect, so no transaction is opened early.
    # MIGRATION_LOCK_TIMEOUT overrides.
    lock_timeout = os.environ.get("MIGRATION_LOCK_TIMEOUT", "5s")
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"options": f"-c lock_timeout={lock_timeout}"},
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
