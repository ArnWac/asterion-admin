"""Real PostgreSQL tenant isolation invariants (plan §PR-1).

These tests prove that schema-per-tenant with ``SET LOCAL search_path``
actually isolates tenant data. SQLite cannot model PostgreSQL schemas, so
these invariants are not exercised by the rest of the test suite.

Skipped automatically unless ``ASTERION_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from asterion.models.tenant_rbac import TenantRole
from asterion.tenancy.schema_strategy import set_search_path

pytestmark = pytest.mark.postgres


async def _insert_role(session, *, name: str) -> None:
    session.add(TenantRole(name=name, description=name, is_system=False))


async def _list_role_names(session) -> set[str]:
    result = await session.execute(select(TenantRole.name))
    return set(result.scalars().all())


@pytest.mark.asyncio
async def test_tenant_a_writes_are_invisible_to_tenant_b(pg_schemas, pg_sessionmaker):
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            await _insert_role(session, name="alpha-only")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            names = await _list_role_names(session)
            assert names == set(), f"Tenant B should not see tenant A's roles; saw {names}"


@pytest.mark.asyncio
async def test_tenant_b_writes_are_invisible_to_tenant_a(pg_schemas, pg_sessionmaker):
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            await _insert_role(session, name="beta-only")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            names = await _list_role_names(session)
            assert names == set()


@pytest.mark.asyncio
async def test_each_tenant_sees_only_its_own_data(pg_schemas, pg_sessionmaker):
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            await _insert_role(session, name="alpha-1")
            await _insert_role(session, name="alpha-2")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            await _insert_role(session, name="beta-1")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            assert await _list_role_names(session) == {"alpha-1", "alpha-2"}

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            assert await _list_role_names(session) == {"beta-1"}


@pytest.mark.asyncio
async def test_same_role_name_can_exist_in_each_tenant(pg_schemas, pg_sessionmaker):
    """The TenantRole UniqueConstraint on ``name`` is per-schema. The same
    role name can exist in tenant A and tenant B without collision."""
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            await _insert_role(session, name="owner")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            await _insert_role(session, name="owner")

    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            assert await _list_role_names(session) == {"owner"}
        async with session.begin():
            await set_search_path(session, pg_schemas["b"])
            assert await _list_role_names(session) == {"owner"}
