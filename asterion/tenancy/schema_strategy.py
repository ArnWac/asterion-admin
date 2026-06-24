"""Schema-per-tenant search_path helpers.

Tenant isolation: SET LOCAL search_path scopes to the current transaction.
No per-tenant engine cache — use the single shared DatabaseManager engine.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asterion.security.validation import (
    InvalidSchemaNameError,
    validate_schema_name,
)
from asterion.tenancy.context import current_tenant_schema


def _validate_schema_name(schema_name: str) -> None:
    if not schema_name.startswith("tenant_"):
        raise InvalidSchemaNameError(
            f"Tenant schema name must start with 'tenant_': {schema_name!r}"
        )
    validate_schema_name(schema_name)


async def set_search_path(session: AsyncSession, schema_name: str) -> None:
    """SET LOCAL search_path on an existing session. Scoped to current transaction."""
    _validate_schema_name(schema_name)
    await session.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))


async def get_tenant_session(
    schema_name: str,
    db,  # DatabaseManager
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a new AsyncSession scoped to the given tenant schema.

    Prefer set_search_path() on the existing request session when inside a
    request handler. Use this for out-of-request operations (CLI, bootstrap).
    """
    _validate_schema_name(schema_name)
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await set_search_path(session, schema_name)
            yield session


@asynccontextmanager
async def independent_tenant_session(
    db,  # DatabaseManager
) -> AsyncIterator[AsyncSession]:
    """Open an independent transaction scoped to the *current* tenant.

    Use this when domain code needs a write to survive a rollback of the
    surrounding request — counters, lockouts, audit rows, outbox entries — and
    therefore must run in its own transaction with its own commit. A plain
    ``db.session()`` would check out a fresh connection with the default
    ``search_path`` (``public``), so on PostgreSQL it would not see the tenant's
    tables. This helper carries the request's tenant schema across into that new
    transaction, giving the same isolation the request session has.

    The tenant is read from the ``current_tenant_schema`` ContextVar, which
    ``get_async_session`` sets for the request. Calling this outside a
    tenant-scoped request raises ``RuntimeError``; for out-of-request work
    (CLI, bootstrap) use ``get_tenant_session`` with an explicit schema instead.

    On SQLite there is no ``search_path`` (the single shared schema already
    holds every tenant's rows), so the scoping step is skipped and the helper
    degrades to a plain independent transaction.
    """
    schema = current_tenant_schema.get()
    if schema is None:
        raise RuntimeError(
            "independent_tenant_session() requires a tenant in context — call it "
            "inside a tenant-scoped request, or use get_tenant_session() with an "
            "explicit schema out of band."
        )
    async with db.session() as session:
        async with session.begin():
            if session.bind.dialect.name == "postgresql":
                await set_search_path(session, schema)
            yield session
