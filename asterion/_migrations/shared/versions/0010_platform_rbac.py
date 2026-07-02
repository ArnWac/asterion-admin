"""platform_rbac

Revision ID: 0010_platform_rbac
Revises: 0009_data_subject_requests
Create Date: 2026-07-02

Adds the platform-tier RBAC store (ADR-0004): public/global tables that carry
``platform.*`` permission keys, administered by a superadmin. Symmetric to the
tenant RBAC tables but global (a platform operator is not scoped to a tenant),
and with a direct user->role link instead of a membership indirection.

  platform_roles
  platform_role_permissions
  platform_user_roles
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID

# Alembic identifiers
revision = "0010_platform_rbac"
down_revision = "0009_data_subject_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_roles",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_platform_roles_name"),
    )
    op.create_index("ix_platform_roles_name", "platform_roles", ["name"])

    op.create_table(
        "platform_role_permissions",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("role_id", GUID(), nullable=False),
        sa.Column("permission_key", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["platform_roles.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "role_id", "permission_key", name="uq_platform_role_permission_key"
        ),
    )
    op.create_index(
        "ix_platform_role_permissions_role_id", "platform_role_permissions", ["role_id"]
    )
    op.create_index(
        "ix_platform_role_permissions_permission_key",
        "platform_role_permissions",
        ["permission_key"],
    )

    op.create_table(
        "platform_user_roles",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("role_id", GUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["platform_roles.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "role_id", name="uq_platform_user_role"),
    )
    op.create_index("ix_platform_user_roles_user_id", "platform_user_roles", ["user_id"])
    op.create_index("ix_platform_user_roles_role_id", "platform_user_roles", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_platform_user_roles_role_id", table_name="platform_user_roles")
    op.drop_index("ix_platform_user_roles_user_id", table_name="platform_user_roles")
    op.drop_table("platform_user_roles")

    op.drop_index(
        "ix_platform_role_permissions_permission_key",
        table_name="platform_role_permissions",
    )
    op.drop_index(
        "ix_platform_role_permissions_role_id", table_name="platform_role_permissions"
    )
    op.drop_table("platform_role_permissions")

    op.drop_index("ix_platform_roles_name", table_name="platform_roles")
    op.drop_table("platform_roles")
