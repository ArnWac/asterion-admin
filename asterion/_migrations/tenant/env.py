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
from asterion.models.tenant_audit_log import TenantAuditLog  # noqa: F401 — registers table

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

# Theme H: the framework base tree is tracked in its own version table
# (``alembic_version_asterion_tenant``) so it coexists with the downstream
# app's tenant history in the same schema. ``upgrade_tenant_schema`` sets this
# via ``version_table`` on the Config; default ``None`` → Alembic's standard
# ``alembic_version`` for any plain invocation of this env.
_version_table = config.get_main_option("version_table") or None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    opts = {}
    if _schema:
        opts["include_schemas"] = True
    if _version_table:
        opts["version_table"] = _version_table
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, **opts)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    if _schema:
        connection.execute(text(f'SET search_path TO "{_schema}", public'))
        # ``execute`` autobegins a transaction (SQLAlchemy 2.0). Commit it so
        # Alembic's own ``begin_transaction()`` below owns — and commits — the
        # migration DDL. ``SET search_path`` is session-scoped (not SET LOCAL),
        # so it survives this commit. WITHOUT this commit the DDL runs inside
        # the autobegun transaction that the outer ``connect()`` context rolls
        # back on exit, so the tables silently never persist.
        connection.commit()
    opts = {"version_table_schema": _schema}
    if _version_table:
        opts["version_table"] = _version_table
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **opts,
    )
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
