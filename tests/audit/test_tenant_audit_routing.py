"""Per-tenant audit routing (v0.1.13).

Tenant-context admin events go to the tenant schema's ``tenant_audit_logs``
table; global / cross-tenant events stay in the public ``audit_logs`` table.
``audit_payload(tenant_scoped=…)`` is the single switch; the resource routers
set it from ``ctx.tenant``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.admin.policy import ReadOnlyPolicy
from asterion.audit import CRUD_CREATE, audit_payload, record_audit_in_session
from asterion.builtins.admin import (
    BUILTIN_TENANT_ADMINS,
    AuditLogAdmin,
    TenantAuditLogAdmin,
)
from asterion.contract.service import resolve_model_scope
from asterion.models.audit_log import AuditLog
from asterion.models.base import TenantBase
from asterion.models.tenant_audit_log import TenantAuditLog

# ---------------------------------------------------------------------------
# Payload routing (no I/O)
# ---------------------------------------------------------------------------


def test_audit_payload_defaults_to_global():
    tid = uuid.uuid4()
    row = audit_payload(action="login_success", tenant_id=tid)
    assert isinstance(row, AuditLog)
    assert row.tenant_id == tid


def test_audit_payload_tenant_scoped_builds_tenant_row():
    row = audit_payload(action=CRUD_CREATE, tenant_id=uuid.uuid4(), tenant_scoped=True)
    assert isinstance(row, TenantAuditLog)
    # The tenant table has no tenant_id column — the schema *is* the tenant.
    assert not hasattr(row, "tenant_id")
    assert row.action == CRUD_CREATE


# ---------------------------------------------------------------------------
# Model + admin shape
# ---------------------------------------------------------------------------


def test_tenant_audit_log_is_tenant_scoped():
    assert resolve_model_scope(TenantAuditLogAdmin()) == "tenant"
    assert resolve_model_scope(AuditLogAdmin()) == "global"


def test_tenant_and_global_audit_have_distinct_resources():
    # Distinct table names → no AdminRegistry collision when both are
    # registered; A's scope filter shows the right one per context.
    assert TenantAuditLogAdmin().model_name != AuditLogAdmin().model_name
    assert TenantAuditLogAdmin().model_name == "tenant_audit_logs"


def test_tenant_audit_admin_is_read_only_and_registered():
    assert isinstance(TenantAuditLogAdmin.policy, ReadOnlyPolicy)
    assert TenantAuditLogAdmin in BUILTIN_TENANT_ADMINS


# ---------------------------------------------------------------------------
# DB write lands in the tenant table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_audit_in_session_writes_tenant_table(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ta.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(TenantBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await record_audit_in_session(
                    session,
                    action=CRUD_CREATE,
                    tenant_scoped=True,
                    method="POST",
                    path="/api/v1/admin/projects",
                    status_code=201,
                    resource="projects",
                )
            tenant_rows = (
                await session.execute(select(func.count()).select_from(TenantAuditLog))
            ).scalar_one()
        assert tenant_rows == 1
    finally:
        await engine.dispose()
