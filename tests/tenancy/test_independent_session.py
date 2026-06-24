"""Dialect-agnostic behaviour of ``independent_tenant_session``.

The Postgres isolation/persistence invariants live in
``tests/postgres/test_independent_tenant_session.py``. Here we cover the parts
that do not need a real Postgres: the no-tenant guard, and that SQLite (which
has no ``search_path``) degrades to a plain independent transaction.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from asterion.db.session import DatabaseManager
from asterion.models.base import GlobalModel, TenantBase
from asterion.models.tenant_rbac import TenantRole
from asterion.tenancy.context import current_tenant_schema
from asterion.tenancy.schema_strategy import independent_tenant_session


@pytest_asyncio.fixture
async def db(tmp_path):
    # A file-backed SQLite DB so a row committed by the independent session is
    # visible to a later session (separate :memory: connections would not share
    # state).
    url = f"sqlite+aiosqlite:///{tmp_path / 'indep.db'}"
    manager = DatabaseManager(url)
    async with manager.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
        await conn.run_sync(TenantBase.metadata.create_all)
    yield manager
    await manager.dispose()


@pytest.mark.anyio
async def test_raises_without_tenant_in_context(db):
    # ContextVar default is None outside a tenant-scoped request.
    with pytest.raises(RuntimeError, match="requires a tenant in context"):
        async with independent_tenant_session(db):
            pass


@pytest.mark.anyio
async def test_sqlite_degrades_to_plain_independent_transaction(db):
    """No search_path is set on SQLite, and the write still persists."""
    token = current_tenant_schema.set("tenant_demo")
    try:
        async with independent_tenant_session(db) as session:
            session.add(
                TenantRole(name="demo", description="demo", is_system=False)
            )
    finally:
        current_tenant_schema.reset(token)

    async with db.session() as session:
        result = await session.execute(select(TenantRole.name))
        assert "demo" in set(result.scalars().all())
