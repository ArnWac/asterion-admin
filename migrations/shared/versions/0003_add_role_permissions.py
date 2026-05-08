"""add role_permissions

Revision ID: 0003_add_role_permissions
Revises: 0002_add_password_reset_tokens
Create Date: 2026-05-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0003_add_role_permissions"
down_revision = "0002_add_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("can_list", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_create", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_update", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("role_id", "model_name", name="uq_role_permission"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])
    op.create_index("ix_role_permissions_model_name", "role_permissions", ["model_name"])


def downgrade() -> None:
    op.drop_table("role_permissions")
