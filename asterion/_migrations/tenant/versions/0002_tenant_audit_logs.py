"""tenant audit log

Revision ID: 0002_tenant_audit_logs
Revises: 0001_initial_tenant
Create Date: 2026-06-23

Adds the per-tenant ``tenant_audit_logs`` table INSIDE the active
search_path schema. Tenant-context admin events (CRUD / actions /
import-export on tenant-scoped resources) are recorded here instead of
the public ``audit_logs`` table, giving each tenant a physically isolated
audit trail. Mirrors the public ``audit_logs`` columns minus ``tenant_id``
(the schema *is* the tenant).

Existing tenants pick this up via ``asterion db upgrade-tenants``; new
tenants get it during ``bootstrap_tenant``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID

# Alembic identifiers
revision = "0002_tenant_audit_logs"
down_revision = "0001_initial_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent (Theme H): a downstream app that already added this table in
    # its own tenant tree (the common case the framework tree now backstops)
    # must not get a duplicate-table error when the framework base runs.
    if sa.inspect(op.get_bind()).has_table("tenant_audit_logs"):
        return
    op.create_table(
        "tenant_audit_logs",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", GUID(), nullable=True),
        sa.Column("resource", sa.String(100), nullable=True),
        sa.Column("record_id", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=True),
        sa.Column("actor_label", sa.String(255), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tenant_audit_logs_actor_user_id", "tenant_audit_logs", ["actor_user_id"])
    op.create_index("ix_tenant_audit_logs_resource", "tenant_audit_logs", ["resource"])
    op.create_index("ix_tenant_audit_logs_created_at", "tenant_audit_logs", ["created_at"])
    op.create_index(
        "ix_tenant_audit_logs_actor_user_id_created_at",
        "tenant_audit_logs",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_tenant_audit_logs_action_created_at",
        "tenant_audit_logs",
        ["action", "created_at"],
    )
    op.create_index("ix_tenant_audit_logs_record_id", "tenant_audit_logs", ["record_id"])


def downgrade() -> None:
    op.drop_table("tenant_audit_logs")
