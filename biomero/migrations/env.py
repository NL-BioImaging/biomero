from __future__ import annotations
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import BIOMERO Base models
from biomero.database import Base as BIOMEROBase

config = context.config

# Configure URL from env
db_url = os.getenv("SQLALCHEMY_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Use a per-project version table in the same schema
config.set_main_option("version_table", "alembic_version_biomero")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BIOMEROBase.metadata


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in target_metadata.tables
    if type_ == "index":
        tbl = object.table.name if hasattr(object, "table") else None
        return tbl in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        version_table=config.get_main_option("version_table"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
            version_table=config.get_main_option("version_table"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
