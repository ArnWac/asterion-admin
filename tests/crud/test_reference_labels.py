"""Batched reference labels for list views (v0.1.15).

``ModelAdmin.resolve_list_labels`` lets an admin turn raw id columns into
human-readable names; ``list_records`` attaches the result as ``<col>__label``
on each row. The builtin RBAC admins use it so the tenant role/membership
lists show role names + member emails instead of UUIDs — resolved in one
batched query per related table, not one per row.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.builtins.admin import TenantMembershipRoleAdmin, TenantRolePermissionAdmin
from asterion.crud.services import list_records, read_record
from asterion.models.base import GlobalModel, TenantBase
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import TenantMembershipRole, TenantRole, TenantRolePermission
from asterion.models.user import User
from asterion.registry import ModelAdmin


def test_base_resolve_list_labels_is_empty():
    import asyncio

    class _A(ModelAdmin):
        model = TenantRole

    assert asyncio.run(_A().resolve_list_labels([], session=None)) == {}


@pytest.fixture
async def seeded(tmp_path):
    # Global models declare schema="public"; on sqlite that needs the same
    # schema_translate_map the DatabaseManager applies in the real app.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'labels.db'}").execution_options(
        schema_translate_map={"public": None}
    )
    async with engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
        await conn.run_sync(TenantBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = {}
    async with factory() as session:
        async with session.begin():
            user = User(email="alice@example.com", hashed_password="x", is_active=True)
            tenant = Tenant(slug="acme", name="Acme", schema_name="tenant_acme")
            role = TenantRole(name="owner", is_system=True)
            session.add_all([user, tenant, role])
            await session.flush()
            membership = TenantMembership(user_id=user.id, tenant_id=tenant.id, is_active=True)
            session.add(membership)
            await session.flush()
            session.add(TenantMembershipRole(membership_id=membership.id, role_id=role.id))
            session.add(TenantRolePermission(role_id=role.id, permission_key="admin.projects.list"))
            ids = {
                "role_id": str(role.id),
                "role_name": role.name,
                "membership_id": str(membership.id),
                "email": user.email,
            }
    try:
        yield factory, ids
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_membership_role_list_resolves_role_and_member(seeded):
    factory, ids = seeded
    async with factory() as session:
        page = await list_records(session, TenantMembershipRoleAdmin())
    row = page["items"][0]
    assert row["role_id__label"] == ids["role_name"]
    assert row["membership_id__label"] == ids["email"]
    # Raw ids are still present alongside the labels.
    assert row["role_id"] == ids["role_id"]


@pytest.mark.asyncio
async def test_role_permission_list_resolves_role_name(seeded):
    factory, ids = seeded
    async with factory() as session:
        page = await list_records(session, TenantRolePermissionAdmin())
    row = page["items"][0]
    assert row["role_id__label"] == ids["role_name"]
    assert "membership_id__label" not in row


@pytest.mark.asyncio
async def test_membership_role_fk_options_resolve_member_email(seeded):
    """The FK-picker options for the cross-schema membership_id resolve to
    member emails via the membership → user join (no DB FK)."""
    factory, ids = seeded
    async with factory() as session:
        opts = await TenantMembershipRoleAdmin().resolve_fk_options(
            "membership_id", session=session
        )
    assert opts == [{"value": ids["membership_id"], "label": ids["email"]}]


@pytest.mark.asyncio
async def test_membership_role_fk_options_role_id_falls_back(seeded):
    """role_id has a real FK → custom resolver returns None (generic path)."""
    factory, _ = seeded
    async with factory() as session:
        assert (
            await TenantMembershipRoleAdmin().resolve_fk_options("role_id", session=session)
            is None
        )


@pytest.mark.asyncio
async def test_detail_read_resolves_labels(seeded):
    """The detail (read) path attaches the same labels as the list view."""
    from sqlalchemy import select

    factory, ids = seeded
    async with factory() as session:
        row_id = (await session.execute(select(TenantMembershipRole.id))).scalars().first()
        payload = await read_record(session, TenantMembershipRoleAdmin(), str(row_id))
    assert payload["role_id__label"] == ids["role_name"]
    assert payload["membership_id__label"] == ids["email"]
