"""Alembic environment for tenant-local schemas.

Run against a specific tenant schema by passing -x schema=<name>:

    alembic -c alembic_tenant.ini -x schema=tenant_acme upgrade head

Reads DATABASE_URL from the environment. No dependency on legacy settings.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

import asterion.models.tenant_rbac  # noqa: F401 — registers TenantBase tables
from asterion.models.base import TenantBase

config = context.config

_database_url = (
    os.environ.get("ASTERION_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or config.get_main_option("sqlalchemy.url")
)
if not _database_url:
    raise RuntimeError(
        "Set ASTERION_DATABASE_URL (or DATABASE_URL), or set "
        "'sqlalchemy.url' on the alembic Config before running."
    )
config.set_main_option("sqlalchemy.url", _database_url)

if config.config_file_name is not None:
    # disable_existing_loggers=False so loggers created before alembic
    # was invoked (e.g. asterion.access) stay enabled.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = TenantBase.metadata

_schema = context.get_x_argument(as_dictionary=True).get("schema")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    opts = {}
    if _schema:
        opts["include_schemas"] = True
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, **opts)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    if _schema:
        connection.execute(text(f'SET search_path TO "{_schema}", public'))
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
