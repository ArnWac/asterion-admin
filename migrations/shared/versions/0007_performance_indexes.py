"""performance_indexes

Revision ID: 0007_performance_indexes
Revises: 0006_tenant_scoped_roles
Create Date: 2026-05-11

Add indexes to audit_logs and revoked_tokens for faster filtered queries.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_performance_indexes"
down_revision = "0006_tenant_scoped_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", "created_at"])
    op.create_index("ix_audit_logs_tenant_id_created_at", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])
    op.create_index("ix_audit_logs_object_id", "audit_logs", ["object_id"])
    op.create_index("ix_revoked_tokens_exp", "revoked_tokens", ["exp"])


def downgrade() -> None:
    op.drop_index("ix_revoked_tokens_exp", table_name="revoked_tokens")
    op.drop_index("ix_audit_logs_object_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_id_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
