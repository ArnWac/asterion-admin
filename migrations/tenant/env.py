"""Tenant migration environment.

Run against a specific tenant schema by passing --x-schema:

    alembic -c alembic_tenant.ini -x schema=tenant_acme upgrade head

When --x-schema is supplied the migration connection sets search_path so that
DDL runs inside that tenant schema.  Without it, migrations run in the default
search_path (useful for generating SQL scripts).
"""
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from adminfoundry.settings import settings
from adminfoundry.models.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Optional schema override passed via -x schema=tenant_acme
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
        connection.execute(text(f"SET search_path TO {_schema}, public"))
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
