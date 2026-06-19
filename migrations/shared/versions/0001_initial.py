"""initial public schema

Revision ID: 0001_initial_public
Revises:
Create Date: 2026-05-20

Creates the v1 global/public tables:

  users
  tenants
  tenant_memberships
  permission_catalog
  audit_logs
  impersonation_logs

Tables are created without a schema qualifier so they land in the default
search_path. On PostgreSQL deployments that's ``public``; on SQLite
(testing) there are no schemas.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID


# Alembic identifiers
revision = "0001_initial_public"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "tenants",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("date_format", sa.String(16), nullable=True),
        sa.Column("date_pattern", sa.String(64), nullable=True),
        sa.Column("allowed_cidrs", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.UniqueConstraint("schema_name", name="uq_tenants_schema_name"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("ix_tenants_schema_name", "tenants", ["schema_name"], unique=True)

    op.create_table(
        "tenant_memberships",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
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
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_tenant_memberships_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE",
            name="fk_tenant_memberships_tenant_id",
        ),
        sa.UniqueConstraint(
            "user_id", "tenant_id", name="uq_membership_user_tenant",
        ),
    )
    op.create_index(
        "ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"]
    )
    op.create_index(
        "ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"]
    )
    op.create_index(
        "ix_membership_tenant_active",
        "tenant_memberships",
        ["tenant_id", "is_active"],
    )

    op.create_table(
        "permission_catalog",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("source", sa.String(200), nullable=True),
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
        sa.UniqueConstraint("key", name="uq_permission_catalog_key"),
    )
    op.create_index(
        "ix_permission_catalog_key", "permission_catalog", ["key"], unique=True
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=True),
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
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index(
        "ix_audit_logs_actor_user_id_created_at",
        "audit_logs",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_tenant_id_created_at",
        "audit_logs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_action_created_at",
        "audit_logs",
        ["action", "created_at"],
    )
    op.create_index("ix_audit_logs_record_id", "audit_logs", ["record_id"])

    op.create_table(
        "impersonation_logs",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("superadmin_id", GUID(), nullable=False),
        sa.Column("target_user_id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=True),
        sa.Column("jti", sa.String(100), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("jti", name="uq_impersonation_logs_jti"),
    )
    op.create_index(
        "ix_impersonation_logs_superadmin_id",
        "impersonation_logs",
        ["superadmin_id"],
    )
    op.create_index(
        "ix_impersonation_logs_target_user_id",
        "impersonation_logs",
        ["target_user_id"],
    )
    op.create_index(
        "ix_impersonation_logs_tenant_id",
        "impersonation_logs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_impersonation_logs_jti", "impersonation_logs", ["jti"], unique=True
    )


def downgrade() -> None:
    op.drop_table("impersonation_logs")
    op.drop_table("audit_logs")
    op.drop_table("permission_catalog")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
    op.drop_table("users")
