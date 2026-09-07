"""Alembic environment.

Uses a synchronous engine (psycopg) derived from app settings. Imports the model
package so ``target_metadata`` sees every table for autogeneration.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.models import Base  # noqa: F401  (registers all models on Base.metadata)

config = context.config
# Escape literal '%' before handing the URL to Alembic's ConfigParser, whose interpolation
# would otherwise raise on a password/credential containing '%' (e.g. the %40 that PostgresDsn
# emits for an '@' in POSTGRES_PASSWORD). run_migrations_offline uses the raw value directly.
config.set_main_option("sqlalchemy.url", settings.alembic_database_uri.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Raw functional/expression indexes are created via ``op.execute`` (they cannot be declared
# on a model), so autogenerate never sees them in ``target_metadata`` and would otherwise
# emit a spurious ``drop_index`` for them on every revision. Exclude them from comparison.
_AUTOGEN_IGNORED_INDEXES = {"ix_document_chunks_content_fts"}


def _include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001
    return not (type_ == "index" and name in _AUTOGEN_IGNORED_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.alembic_database_uri,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=_include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
