"""Tests for tenant bootstrap orchestration.

The seeding logic is DB-agnostic: we exercise it on SQLite by creating both
the global and tenant metadata on the same engine. Full Postgres
schema-per-tenant provisioning is out of scope for unit tests; it is covered
by the integration tests against a real Postgres instance.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel, TenantBase
from asterion.models.permission_catalog import PermissionCatalog
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.models.user import User
from asterion.tenancy.bootstrap import (
    assign_owner_membership,
    bootstrap_tenant,
    create_tenant_record,
    seed_default_tenant_roles,
)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with eng.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
        await conn.run_sync(TenantBase.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        async with s.begin():
            yield s


async def _seed_catalog(session) -> None:
    session.add_all(
        [
            PermissionCatalog(key="admin.users.list"),
            PermissionCatalog(key="admin.users.read"),
            PermissionCatalog(key="admin.users.delete"),
            PermissionCatalog(key="admin.audit_logs.list"),
            PermissionCatalog(key="admin.audit_logs.delete"),
            PermissionCatalog(key="admin.projects.list"),
            PermissionCatalog(key="admin.projects.create"),
        ]
    )
    await session.flush()


# --- create_tenant_record ---


@pytest.mark.asyncio
async def test_create_tenant_record_creates_row(session):
    tenant = await create_tenant_record(session, name="Acme", slug="acme")
    assert tenant.slug == "acme"
    assert tenant.schema_name == "tenant_acme"
    assert tenant.is_active is True


@pytest.mark.asyncio
async def test_create_tenant_record_idempotent(session):
    a = await create_tenant_record(session, name="Acme", slug="acme")
    b = await create_tenant_record(session, name="ignored", slug="acme")
    assert a.id == b.id


@pytest.mark.asyncio
async def test_create_tenant_record_rejects_invalid_slug(session):
    with pytest.raises(Exception):
        await create_tenant_record(session, name="X", slug="Invalid Slug!")


# --- assign_owner_membership ---


@pytest.mark.asyncio
async def test_assign_owner_membership_creates_and_is_idempotent(session):
    user = User(
        email="owner@example.com",
        hashed_password=hash_password("pw-strong-1"),
        is_active=True,
    )
    session.add(user)
    await session.flush()

    tenant = await create_tenant_record(session, name="Acme", slug="acme")

    m1 = await assign_owner_membership(session, tenant=tenant, user=user)
    m2 = await assign_owner_membership(session, tenant=tenant, user=user)
    assert m1.id == m2.id
    assert m1.is_active is True


@pytest.mark.asyncio
async def test_assign_owner_membership_reactivates_inactive(session):
    user = User(
        email="owner@example.com",
        hashed_password=hash_password("pw-strong-1"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    tenant = await create_tenant_record(session, name="Acme", slug="acme")

    m = await assign_owner_membership(session, tenant=tenant, user=user)
    m.is_active = False
    await session.flush()

    m_again = await assign_owner_membership(session, tenant=tenant, user=user)
    assert m_again.id == m.id
    assert m_again.is_active is True


# --- seed_default_tenant_roles ---


@pytest.mark.asyncio
async def test_seed_creates_three_default_roles(session):
    await _seed_catalog(session)
    roles = await seed_default_tenant_roles(session, session)
    assert set(roles.keys()) == {"owner", "admin", "viewer"}
    for role in roles.values():
        assert role.is_system is True


@pytest.mark.asyncio
async def test_seed_owner_has_admin_star(session):
    await _seed_catalog(session)
    roles = await seed_default_tenant_roles(session, session)
    owner_perms = {
        row.permission_key
        for row in (
            await session.execute(
                TenantRolePermission.__table__.select().where(
                    TenantRolePermission.role_id == roles["owner"].id
                )
            )
        ).mappings()
    }
    assert "admin.*" in owner_perms


@pytest.mark.asyncio
async def test_seed_admin_excludes_deny_list(session):
    await _seed_catalog(session)
    roles = await seed_default_tenant_roles(session, session)
    admin_perms = {
        row.permission_key
        for row in (
            await session.execute(
                TenantRolePermission.__table__.select().where(
                    TenantRolePermission.role_id == roles["admin"].id
                )
            )
        ).mappings()
    }
    assert "admin.users.list" in admin_perms
    assert "admin.audit_logs.list" in admin_perms
    # Deny-list invariants: admin role must never receive these two delete keys.
    # If renamed, update _ADMIN_PERMISSIONS_DENY in tenancy/bootstrap.py.
    assert "admin.audit_logs.delete" not in admin_perms
    assert "admin.users.delete" not in admin_perms


@pytest.mark.asyncio
async def test_seed_viewer_only_gets_list_keys(session):
    await _seed_catalog(session)
    roles = await seed_default_tenant_roles(session, session)
    viewer_perms = {
        row.permission_key
        for row in (
            await session.execute(
                TenantRolePermission.__table__.select().where(
                    TenantRolePermission.role_id == roles["viewer"].id
                )
            )
        ).mappings()
    }
    for key in viewer_perms:
        assert key.endswith(".list"), key


@pytest.mark.asyncio
async def test_seed_is_idempotent(session):
    await _seed_catalog(session)
    await seed_default_tenant_roles(session, session)
    role_count_a = (await session.execute(TenantRole.__table__.select())).fetchall()
    perm_count_a = (await session.execute(TenantRolePermission.__table__.select())).fetchall()

    await seed_default_tenant_roles(session, session)
    role_count_b = (await session.execute(TenantRole.__table__.select())).fetchall()
    perm_count_b = (await session.execute(TenantRolePermission.__table__.select())).fetchall()

    assert len(role_count_a) == len(role_count_b) == 3
    assert len(perm_count_a) == len(perm_count_b)


@pytest.mark.asyncio
async def test_seed_assigns_owner_membership_role(session):
    await _seed_catalog(session)
    user = User(
        email="owner@example.com",
        hashed_password=hash_password("pw-strong-1"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    tenant = await create_tenant_record(session, name="Acme", slug="acme")
    membership = await assign_owner_membership(session, tenant=tenant, user=user)

    roles = await seed_default_tenant_roles(session, session, owner_membership_id=membership.id)

    rows = (
        await session.execute(
            TenantMembershipRole.__table__.select().where(
                TenantMembershipRole.membership_id == membership.id
            )
        )
    ).fetchall()
    assert len(rows) == 1
    assert rows[0].role_id == roles["owner"].id


@pytest.mark.asyncio
async def test_seed_assignment_idempotent(session):
    await _seed_catalog(session)
    user = User(
        email="owner@example.com",
        hashed_password=hash_password("pw-strong-1"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    tenant = await create_tenant_record(session, name="Acme", slug="acme")
    membership = await assign_owner_membership(session, tenant=tenant, user=user)

    await seed_default_tenant_roles(session, session, owner_membership_id=membership.id)
    await seed_default_tenant_roles(session, session, owner_membership_id=membership.id)

    rows = (
        await session.execute(
            TenantMembershipRole.__table__.select().where(
                TenantMembershipRole.membership_id == membership.id
            )
        )
    ).fetchall()
    assert len(rows) == 1


# --- bootstrap_tenant on non-Postgres is a no-op ---


@pytest.mark.asyncio
async def test_bootstrap_tenant_skips_on_non_postgres(session):
    """Calling bootstrap_tenant with a SQLite URL must not raise and must
    not create any tenant-local rows by itself (schema-per-tenant is
    PostgreSQL only)."""
    await bootstrap_tenant(
        "acme",
        session,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    rows = (await session.execute(TenantRole.__table__.select())).fetchall()
    assert rows == []
