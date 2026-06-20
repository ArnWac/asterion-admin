"""2FA: users.totp_* columns + two_factor_backup_codes

Revision ID: 0004_two_factor
Revises: 0003_password_reset_tokens
Create Date: 2026-05-29

Adds TOTP 2FA support (Roadmap 3.4):
  users.totp_secret   (nullable base32 secret)
  users.totp_enabled  (bool, default false)
  two_factor_backup_codes  (one hashed single-use code per row)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID

# Alembic identifiers
revision = "0004_two_factor"
down_revision = "0003_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", sa.String(64), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "two_factor_backup_codes",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_two_factor_backup_codes_user_id",
        "two_factor_backup_codes",
        ["user_id"],
    )
    op.create_index(
        "ix_two_factor_backup_codes_code_hash",
        "two_factor_backup_codes",
        ["code_hash"],
    )


def downgrade() -> None:
    op.drop_table("two_factor_backup_codes")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
