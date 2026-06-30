"""G6 — tenant offboarding, PostgreSQL side.

Proves the parts SQLite cannot: the export bundle dumps every table in the
tenant schema, ``DROP SCHEMA … CASCADE`` removes the schema, the public rows
are cleaned up, and a follow-up resolve of a ``drop``-ed slug 404s (the
``Tenant`` row is gone).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select, text

from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.saved_filter import SavedFilter
from asterion.models.tenant import Tenant
from asterion.models.tenant_audit_log import TenantAuditLog
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from asterion.tenancy.offboarding import export_tenant, offboard_tenant
from asterion.tenancy.schema_strategy import set_search_path

pytestmark = pytest.mark.postgres


async def _schema_exists(sessionmaker, schema_name: str) -> bool:
    async with sessionmaker() as session:
        return (
            await session.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": schema_name},
            )
        ).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_offboard_drop_removes_schema_and_public_rows(pg_schemas, pg_sessionmaker):
    db = DatabaseManager(os.environ["ASTERION_TEST_POSTGRES_URL"])
    schema = pg_schemas["a"]
    try:
        # Register the schema as a tenant + seed public child rows.
        async with pg_sessionmaker() as session:
            async with session.begin():
                user = User(email="owner@pg.example", hashed_password="x", full_name="O")
                tenant = Tenant(slug="acmepg", name="Acme", schema_name=schema, is_active=True)
                session.add_all([user, tenant])
                await session.flush()
                tenant_id = tenant.id
                session.add_all(
                    [
                        TenantMembership(user_id=user.id, tenant_id=tenant_id),
                        AuditLog(
                            method="POST",
                            path="/x",
                            status_code=200,
                            action="probe",
                            tenant_id=tenant_id,
                        ),
                        SavedFilter(
                            user_id=str(user.id),
                            tenant_id=str(tenant_id),
                            resource="posts",
                            name="mine",
                            payload={},
                        ),
                    ]
                )

        # Seed a tenant-local audit row so the export dump is non-empty.
        async with pg_sessionmaker() as session:
            async with session.begin():
                await set_search_path(session, schema)
                session.add(
                    TenantAuditLog(method="POST", path="/y", status_code=200, action="probe")
                )

        # Export bundle dumps every schema table (incl. the seeded audit row).
        bundle = await export_tenant(db, "acmepg")
        assert bundle["schema"]["name"] == schema
        assert "tenant_audit_logs" in bundle["schema"]["tables"]
        assert len(bundle["schema"]["tables"]["tenant_audit_logs"]) == 1

        result = await offboard_tenant(db, "acmepg", mode="drop")
        assert result["schema_dropped"] is True
        assert result["public_rows_deleted"]["memberships"] == 1
        assert result["public_rows_deleted"]["audit"] == 1
        assert result["public_rows_deleted"]["saved_filters"] == 1

        # Schema is gone.
        assert not await _schema_exists(pg_sessionmaker, schema)

        # Tenant row gone (a follow-up resolve would 404) + public rows cleaned.
        async with pg_sessionmaker() as session:
            assert (
                await session.execute(select(Tenant).where(Tenant.slug == "acmepg"))
            ).scalar_one_or_none() is None
            assert (
                await session.execute(select(func.count(TenantMembership.id)))
            ).scalar_one() == 0
            # The offboard audit row (tenant_id NULL) survives the cleanup.
            offboard_rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "tenant_offboard")
                    )
                )
                .scalars()
                .all()
            )
            assert len(offboard_rows) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_offboard_archive_keeps_tombstone_but_drops_schema(pg_schemas, pg_sessionmaker):
    db = DatabaseManager(os.environ["ASTERION_TEST_POSTGRES_URL"])
    schema = pg_schemas["b"]
    try:
        async with pg_sessionmaker() as session:
            async with session.begin():
                session.add(Tenant(slug="archpg", name="Arch", schema_name=schema, is_active=True))

        result = await offboard_tenant(db, "archpg", mode="archive")
        assert result["schema_dropped"] is True

        # Schema dropped, but the Tenant row survives as an inactive tombstone.
        assert not await _schema_exists(pg_sessionmaker, schema)
        async with pg_sessionmaker() as session:
            tenant = (
                await session.execute(select(Tenant).where(Tenant.slug == "archpg"))
            ).scalar_one()
            assert tenant.is_active is False
            assert tenant.offboarded_at is not None
    finally:
        await db.dispose()
