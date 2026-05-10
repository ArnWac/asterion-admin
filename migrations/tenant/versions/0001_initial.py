"""initial tenant schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-06

Tenant schemas are created via `adminfoundry create-tenant <slug>` or via the
tenants router POST /api/v1/tenants/{id}/provision.

Tables that should live in each tenant schema (tenant_scoped=True ModelAdmin
registrations) must be added here so that `alembic -c alembic_tenant.ini
upgrade head` applies them to every tenant schema.

Shared tables (users, roles, role_permissions, audit_log, tenants) remain in
the public schema and are managed by the shared migrations.

Example — add your app's tenant-scoped tables here:

    def upgrade() -> None:
        op.create_table(
            "projects",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("active", sa.Boolean, nullable=False, default=True),
            sa.Column("tenant_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
