"""G6 — tenant offboarding, SQLite side.

The schema drop is a PostgreSQL operation (``tests/postgres/test_tenant_offboard.py``
covers the full lifecycle incl. ``DROP SCHEMA … CASCADE`` and the follow-up 404).
Here we cover the DB-agnostic behaviour: the export bundle shape, public-row
cleanup, the archive/drop distinction on the ``Tenant`` row, the audit trail, and
not-found handling.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.audit.service import TENANT_OFFBOARD
from asterion.auth.password import hash_password
from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.saved_filter import SavedFilter
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from asterion.tenancy.offboarding import (
    TenantNotFoundError,
    export_tenant,
    offboard_tenant,
)


@pytest.fixture
async def db(tmp_path):
    manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'offboard.db'}")
    async with manager.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    yield manager
    await manager.dispose()


async def _seed(db: DatabaseManager) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a tenant with a user/membership + tenant-scoped audit + saved filter.

    Returns ``(tenant_id, user_id)``.
    """
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            user = User(
                email="owner@example.com",
                hashed_password=hash_password("hunter2-strong"),
                full_name="Owner",
            )
            tenant = Tenant(name="Acme", slug="acme", schema_name="tenant_acme")
            session.add_all([user, tenant])
            await session.flush()
            session.add_all(
                [
                    TenantMembership(user_id=user.id, tenant_id=tenant.id),
                    AuditLog(
                        method="POST",
                        path="/x",
                        status_code=200,
                        action="probe",
                        tenant_id=tenant.id,
                    ),
                    SavedFilter(
                        user_id=str(user.id),
                        tenant_id=str(tenant.id),
                        resource="posts",
                        name="mine",
                        payload={},
                    ),
                ]
            )
            return tenant.id, user.id


async def test_export_bundle_shape_on_sqlite(db):
    tenant_id, _ = await _seed(db)

    bundle = await export_tenant(db, "acme")

    assert bundle["tenant"]["slug"] == "acme"
    assert bundle["tenant"]["id"] == str(tenant_id)
    assert len(bundle["memberships"]) == 1
    # SQLite cannot dump a per-tenant schema; the bundle says so honestly.
    assert bundle["schema"] is None
    assert "schema_note" in bundle


async def test_export_not_found_raises(db):
    with pytest.raises(TenantNotFoundError):
        await export_tenant(db, "ghost")


async def test_offboard_archive_keeps_tombstone_row(db):
    await _seed(db)

    result = await offboard_tenant(db, "acme", mode="archive")

    assert result["mode"] == "archive"
    assert result["schema_dropped"] is False  # SQLite
    assert result["public_rows_deleted"] == {
        "memberships": 1,
        "audit": 1,
        "impersonations": 0,
        "saved_filters": 1,
    }

    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "acme"))).scalar_one()
        assert tenant.is_active is False
        assert tenant.offboarded_at is not None
        # Public child rows are gone.
        assert (await session.execute(select(TenantMembership))).scalars().all() == []
        assert (await session.execute(select(SavedFilter))).scalars().all() == []


async def test_offboard_drop_deletes_tenant_row(db):
    await _seed(db)

    result = await offboard_tenant(db, "acme", mode="drop")
    assert result["mode"] == "drop"

    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        assert (
            await session.execute(select(Tenant).where(Tenant.slug == "acme"))
        ).scalar_one_or_none() is None


async def test_offboard_writes_audit_row(db):
    await _seed(db)
    await offboard_tenant(db, "acme", mode="drop")

    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == TENANT_OFFBOARD)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    changes = rows[0].changes
    assert changes["slug"] == "acme"
    assert changes["mode"] == "drop"
    # PII-free summary only — no tenant data leaks into the audit row.
    assert set(changes) == {
        "slug",
        "schema_name",
        "mode",
        "schema_dropped",
        "public_rows_deleted",
    }


async def test_offboard_not_found_raises(db):
    with pytest.raises(TenantNotFoundError):
        await offboard_tenant(db, "ghost")


async def test_offboard_rejects_unknown_mode(db):
    await _seed(db)
    with pytest.raises(ValueError, match="Unknown offboard mode"):
        await offboard_tenant(db, "acme", mode="nuke")  # type: ignore[arg-type]


async def test_offboard_drop_is_idempotent_for_missing_slug(db):
    await _seed(db)
    await offboard_tenant(db, "acme", mode="drop")
    # Second run: the slug is gone, so it raises not-found (caller treats as no-op).
    with pytest.raises(TenantNotFoundError):
        await offboard_tenant(db, "acme", mode="drop")
