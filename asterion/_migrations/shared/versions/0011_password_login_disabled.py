"""rename users.is_service_account -> users.password_login_disabled

Revision ID: 0011_password_login_disabled
Revises: 0010_platform_rbac
Create Date: 2026-07-02

ADR-0005: the "service account" concept moves to an extension; core keeps only a
generic auth mechanism — "this account may not authenticate with a password (and
never receives a password-reset token)". The old ``is_service_account`` flag is
renamed to ``password_login_disabled``; the rename is value-preserving (every
service account was password-login-disabled, and only service accounts carried
the flag), so no data migration is needed. Invited-but-passwordless humans keep
``False`` and still receive reset tokens.
"""
from __future__ import annotations

from alembic import op

# Alembic identifiers
revision = "0011_password_login_disabled"
down_revision = "0010_platform_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("is_service_account", new_column_name="password_login_disabled")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_login_disabled", new_column_name="is_service_account")
