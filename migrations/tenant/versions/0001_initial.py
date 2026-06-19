"""initial tenant schema

Revision ID: 0001_initial_tenant
Revises:
Create Date: 2026-05-20

Creates the v1 tenant-local tables INSIDE the active search_path schema:

  tenant_roles
  tenant_role_permissions
  tenant_membership_roles

Run with::

    alembic -c alembic_tenant.ini -x schema=tenant_<slug> upgrade head

The env.py reads ``-x schema=<name>`` and issues ``SET search_path TO
<name>, public`` before applying. Without ``-x schema`` the migration
runs against the connection's default search_path — useful for offline
SQL generation only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID


# Alembic identifiers
revision = "0001_initial_tenant"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_roles",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.UniqueConstraint("name", name="uq_tenant_roles_name"),
    )
    op.create_index("ix_tenant_roles_name", "tenant_roles", ["name"])

    op.create_table(
        "tenant_role_permissions",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("role_id", GUID(), nullable=False),
        sa.Column("permission_key", sa.String(200), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["role_id"], ["tenant_roles.id"], ondelete="CASCADE",
            name="fk_tenant_role_permissions_role_id",
        ),
        sa.UniqueConstraint(
            "role_id", "permission_key", name="uq_tenant_role_permission_key",
        ),
    )
    op.create_index(
        "ix_tenant_role_permissions_role_id",
        "tenant_role_permissions",
        ["role_id"],
    )
    op.create_index(
        "ix_tenant_role_permissions_permission_key",
        "tenant_role_permissions",
        ["permission_key"],
    )

    op.create_table(
        "tenant_membership_roles",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("membership_id", GUID(), nullable=False),
        sa.Column("role_id", GUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["role_id"], ["tenant_roles.id"], ondelete="CASCADE",
            name="fk_tenant_membership_roles_role_id",
        ),
        sa.UniqueConstraint(
            "membership_id", "role_id", name="uq_tenant_membership_role",
        ),
    )
    op.create_index(
        "ix_tenant_membership_roles_membership_id",
        "tenant_membership_roles",
        ["membership_id"],
    )
    op.create_index(
        "ix_tenant_membership_roles_role_id",
        "tenant_membership_roles",
        ["role_id"],
    )


def downgrade() -> None:
    op.drop_table("tenant_membership_roles")
    op.drop_table("tenant_role_permissions")
    op.drop_table("tenant_roles")
