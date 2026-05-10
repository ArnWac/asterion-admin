"""tenant_scoped_roles

Revision ID: 0006_tenant_scoped_roles
Revises: 0005_add_role_description
Create Date: 2026-05-11

Adds tenant_id to roles and role_permissions so that roles can be scoped
per-tenant.  NULL tenant_id = global/superadmin role (backwards compatible).

The old global unique constraint on roles.name is replaced with a per-tenant
unique constraint (name, tenant_id).  SQLite does not support DROP CONSTRAINT;
for SQLite dev environments delete the .db file and let create_all recreate it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_tenant_scoped_roles"
down_revision = "0005_add_role_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column("roles", sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])

    op.add_column("role_permissions", sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_role_permissions_tenant_id", "role_permissions", ["tenant_id"])

    if not is_sqlite:
        # Replace global unique on name with per-tenant unique.
        # The old constraint name depends on how it was created; try both common forms.
        try:
            op.drop_index("ix_roles_name", table_name="roles")
        except Exception:
            pass
        try:
            op.drop_constraint("uq_roles_name", "roles", type_="unique")
        except Exception:
            pass
        op.create_index("uq_roles_name_tenant", "roles", ["name", "tenant_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if not is_sqlite:
        op.drop_index("uq_roles_name_tenant", table_name="roles")
        op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.drop_index("ix_role_permissions_tenant_id", table_name="role_permissions")
    op.drop_column("role_permissions", "tenant_id")

    op.drop_index("ix_roles_tenant_id", table_name="roles")
    op.drop_column("roles", "tenant_id")
