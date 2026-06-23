"""The example's global admins resolve ids to human-readable labels.

`TenantMembershipAdmin` and `ImpersonationLogAdmin` override
`resolve_list_labels` so the global tables show emails / slugs instead of raw
UUIDs. Labels are resolved in batched queries (one per related table).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.models.base import GlobalModel
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from examples.multi_tenant.global_admins import ImpersonationLogAdmin, TenantMembershipAdmin


@pytest.fixture
async def seeded(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'glabels.db'}"
    ).execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    data = {}
    async with factory() as session:
        async with session.begin():
            su = User(
                email="root@example.com", hashed_password="x", is_active=True, is_superadmin=True
            )
            owner = User(email="owner@acme.example.com", hashed_password="x", is_active=True)
            tenant = Tenant(slug="acme", name="Acme", schema_name="tenant_acme")
            session.add_all([su, owner, tenant])
            await session.flush()
            membership = TenantMembership(user_id=owner.id, tenant_id=tenant.id, is_active=True)
            implog = ImpersonationLog(
                superadmin_id=su.id,
                target_user_id=owner.id,
                tenant_id=tenant.id,
                jti=uuid.uuid4().hex,
            )
            session.add_all([membership, implog])
            await session.flush()
            data = {
                "membership": membership,
                "implog": implog,
                "su_id": str(su.id),
                "owner_id": str(owner.id),
                "tenant_id": str(tenant.id),
            }
    try:
        yield factory, data
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_membership_admin_labels(seeded):
    factory, data = seeded
    async with factory() as session:
        labels = await TenantMembershipAdmin().resolve_list_labels(
            [data["membership"]], session=session
        )
    assert labels["user_id"][data["owner_id"]] == "owner@acme.example.com"
    assert labels["tenant_id"][data["tenant_id"]] == "acme"


@pytest.mark.asyncio
async def test_impersonation_log_admin_labels(seeded):
    factory, data = seeded
    async with factory() as session:
        labels = await ImpersonationLogAdmin().resolve_list_labels(
            [data["implog"]], session=session
        )
    assert labels["superadmin_id"][data["su_id"]] == "root@example.com"
    assert labels["target_user_id"][data["owner_id"]] == "owner@acme.example.com"
    assert labels["tenant_id"][data["tenant_id"]] == "acme"
