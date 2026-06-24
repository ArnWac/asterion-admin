"""``independent_tenant_session`` proves persistence *and* isolation.

The framework gap (surfaced from Simpletimes): domain code sometimes needs a
write to outlive a rollback of the surrounding request — a PIN-failure counter
must survive the punch rollback. That requires a *separate* transaction with
its own commit. A plain ``db.session()`` checks out a fresh connection with the
default ``search_path`` (``public``) and so cannot see the tenant's tables.
``independent_tenant_session`` carries the request's tenant schema into that new
transaction.

This test pins the acceptance criteria: inside a request scoped to tenant A,
open an independent session, write a tenant-table row and commit it, then roll
back the surrounding request. The committed row must (1) persist and (2) live in
schema A — not ``public``, not schema B.

Skipped automatically unless ``ASTERION_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from asterion.db.session import DatabaseManager
from asterion.models.tenant_rbac import TenantRole
from asterion.tenancy.context import current_tenant_schema
from asterion.tenancy.schema_strategy import (
    independent_tenant_session,
    set_search_path,
)
from tests.postgres.conftest import _postgres_url

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def db():
    url = _postgres_url()
    if url is None:
        pytest.skip("Set ASTERION_TEST_POSTGRES_URL to run.")
    manager = DatabaseManager(url)
    yield manager
    await manager.dispose()


async def _role_names(session) -> set[str]:
    result = await session.execute(select(TenantRole.name))
    return set(result.scalars().all())


@pytest.mark.asyncio
async def test_independent_write_survives_request_rollback_in_tenant_schema(
    db,
    pg_schemas,
):
    """Persistence + isolation in one shot."""
    token = current_tenant_schema.set(pg_schemas["a"])
    try:
        # Surrounding request, scoped to tenant A, that ultimately rolls back.
        with pytest.raises(RuntimeError, match="force request rollback"):
            async with db.session() as request_session:
                async with request_session.begin():
                    await set_search_path(request_session, pg_schemas["a"])
                    request_session.add(
                        TenantRole(
                            name="rolled-back",
                            description="rolled-back",
                            is_system=False,
                        )
                    )
                    await request_session.flush()

                    # Independent, tenant-scoped transaction: commits on its own.
                    async with independent_tenant_session(db) as indep:
                        indep.add(
                            TenantRole(
                                name="persisted",
                                description="persisted",
                                is_system=False,
                            )
                        )

                    raise RuntimeError("force request rollback")
    finally:
        current_tenant_schema.reset(token)

    # Schema A: the independent write persisted; the request write rolled back.
    async with db.session() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            assert await _role_names(session) == {"persisted"}

    # Schema B: never saw either write.
    async with db.session() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            assert await _role_names(session) == set()


@pytest.mark.asyncio
async def test_independent_session_is_a_separate_transaction(db, pg_schemas):
    """The independent session does not join the caller's transaction: its rows
    are visible to a later connection even before the caller commits/rolls back.
    """
    token = current_tenant_schema.set(pg_schemas["a"])
    try:
        async with db.session() as request_session:
            async with request_session.begin():
                await set_search_path(request_session, pg_schemas["a"])
                async with independent_tenant_session(db) as indep:
                    indep.add(
                        TenantRole(
                            name="committed-early",
                            description="committed-early",
                            is_system=False,
                        )
                    )
                # indep committed here, while request_session txn is still open.
                async with db.session() as other:
                    async with other.begin():
                        await set_search_path(other, pg_schemas["a"])
                        assert "committed-early" in await _role_names(other)
    finally:
        current_tenant_schema.reset(token)
