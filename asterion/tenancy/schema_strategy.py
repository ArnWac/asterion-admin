"""Schema-per-tenant search_path helpers.

Tenant isolation: SET LOCAL search_path scopes to the current transaction.
No per-tenant engine cache — use the single shared DatabaseManager engine.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asterion.security.validation import (
    InvalidSchemaNameError,
    validate_schema_name,
)


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
