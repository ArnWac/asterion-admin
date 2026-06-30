"""G3 — audit retention across public + every tenant schema (PostgreSQL).

Proves :func:`apply_retention` prunes old rows from the public ``audit_logs``
AND from each tenant schema's ``tenant_audit_logs`` (by switching
``search_path`` per tenant), while keeping rows newer than the cutoff.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.tenant import Tenant
from asterion.models.tenant_audit_log import TenantAuditLog
from asterion.privacy.retention import apply_retention
from asterion.tenancy.schema_strategy import set_search_path

pytestmark = pytest.mark.postgres


def _old() -> datetime:
    return datetime.now(UTC) - timedelta(days=120)


def _recent() -> datetime:
    return datetime.now(UTC) - timedelta(days=5)


def _tenant_audit(created: datetime) -> TenantAuditLog:
    return TenantAuditLog(
        method="POST",
        path="/x",
        status_code=200,
        action="probe",
        created_at=created,
        updated_at=created,
    )


@pytest.mark.asyncio
async def test_apply_retention_prunes_public_and_each_tenant(pg_schemas, pg_sessionmaker):
    db = DatabaseManager(os.environ["ASTERION_TEST_POSTGRES_URL"])
    try:
        # Register the two schemas as tenants so apply_retention discovers them.
        async with pg_sessionmaker() as session:
            async with session.begin():
                session.add_all(
                    [
                        Tenant(slug="a", name="A", schema_name=pg_schemas["a"], is_active=True),
                        Tenant(slug="b", name="B", schema_name=pg_schemas["b"], is_active=True),
                        AuditLog(
                            method="POST",
                            path="/x",
                            status_code=200,
                            action="probe",
                            created_at=_old(),
                            updated_at=_old(),
                        ),
                        AuditLog(
                            method="POST",
                            path="/y",
                            status_code=200,
                            action="probe",
                            created_at=_recent(),
                            updated_at=_recent(),
                        ),
                    ]
                )

        # One old + one recent tenant_audit row in each schema.
        for key in ("a", "b"):
            async with pg_sessionmaker() as session:
                async with session.begin():
                    await set_search_path(session, pg_schemas[key])
                    session.add_all([_tenant_audit(_old()), _tenant_audit(_recent())])

        results = await apply_retention(db, retention_days=90, all_tenants=True)

        # One old row pruned from public + each tenant schema.
        assert results["public"] == 1
        assert results["a"] == 1
        assert results["b"] == 1

        # The recent row survives everywhere.
        async with pg_sessionmaker() as session:
            public_count = (await session.execute(select(func.count(AuditLog.id)))).scalar_one()
            assert public_count == 1
        for key in ("a", "b"):
            async with pg_sessionmaker() as session:
                await set_search_path(session, pg_schemas[key])
                count = (await session.execute(select(func.count(TenantAuditLog.id)))).scalar_one()
                assert count == 1
    finally:
        await db.dispose()
