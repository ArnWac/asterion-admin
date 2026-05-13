"""SchemaTenantStrategy — per-schema PostgreSQL engine cache with injection guard."""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker

from adminfoundry.settings import settings

# Allowlist for schema names produced from tenant slugs.
# Prevents SQL injection through the SET search_path statement.
# Hyphens are allowed (slugs use them); schema name is always quoted in SQL.
_SAFE_SCHEMA_RE = re.compile(r"^tenant_[a-z0-9]([a-z0-9_-]*[a-z0-9])?$")

_tenant_engines: dict[str, AsyncEngine] = {}


def _validate_schema_name(schema_name: str) -> None:
    if not _SAFE_SCHEMA_RE.match(schema_name):
        raise ValueError(
            f"Unsafe schema name rejected: {schema_name!r}. "
            "Expected format: tenant_<slug> (lowercase alphanumerics and underscores only)."
        )


def _make_tenant_engine(schema_name: str) -> AsyncEngine:
    _validate_schema_name(schema_name)
    eng = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)

    @event.listens_for(eng.sync_engine, "connect")
    def set_search_path(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        # schema_name is validated against _SAFE_SCHEMA_RE; quoted to handle hyphens safely.
        cursor.execute(f'SET search_path TO "{schema_name}", public')
        cursor.close()

    return eng


def get_or_create_tenant_engine(schema_name: str) -> AsyncEngine:
    """Return a cached engine for the given validated schema name."""
    if schema_name not in _tenant_engines:
        if "postgresql" in settings.DATABASE_URL:
            _tenant_engines[schema_name] = _make_tenant_engine(schema_name)
        else:
            # SQLite has no schema support — reuse the shared engine for tests
            from adminfoundry.database import engine
            _tenant_engines[schema_name] = engine
    return _tenant_engines[schema_name]


async def get_tenant_session(schema_name: str) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession scoped to the given tenant schema."""
    tenant_engine = get_or_create_tenant_engine(schema_name)
    factory = async_sessionmaker(tenant_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
