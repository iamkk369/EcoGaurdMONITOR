"""Alembic environment configuration for EcoGuardAI.

Uses the synchronous database URL from settings (Alembic runs migrations
synchronously; the async engine is only used by the running application).

Run all pending migrations:
    alembic upgrade head

Rollback the last migration:
    alembic downgrade -1

Auto-generate a new migration from model changes:
    alembic revision --autogenerate -m "describe your change"
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so `config` and `core` are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Base so Alembic can inspect the metadata for autogenerate support.
# All ORM models are registered on Base.metadata when this module is imported.
from core.database import Base
from config.settings import settings

# Expose metadata to Alembic autogenerate
target_metadata = Base.metadata


def _get_url() -> str:
    """Return a synchronous (psycopg2-compatible) database URL.

    Alembic uses a synchronous connection for migrations.  The application
    engine uses asyncpg (`postgresql+asyncpg://`), so we strip that driver
    prefix here to get the plain `postgresql://` URL that psycopg2 or
    pg8000 can use.
    """
    url = settings.database_url  # always the plain postgresql:// form
    return url


def run_migrations_offline() -> None:
    """Run migrations without a live database connection (generates SQL only).

    Useful for generating a SQL script to review before applying.
    Usage: alembic upgrade head --sql
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    # Build a synchronous engine from the plain postgresql:// URL.
    # We override 'sqlalchemy.url' from alembic.ini with the settings value
    # so that .env changes are honoured at migration time.
    alembic_config = context.config
    alembic_config.set_main_option("sqlalchemy.url", _get_url())

    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
