"""users.is_service_account

Revision ID: 0005_user_is_service_account
Revises: 0004_two_factor
Create Date: 2026-06-22

Adds ``users.is_service_account`` (bool, default false) — marks token-only
service / machine accounts (see asterion.auth.service_accounts). Used to exclude
them from the password-reset flow and to identify them in queries / UI.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic identifiers
revision = "0005_user_is_service_account"
down_revision = "0004_two_factor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_service_account",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_service_account")
