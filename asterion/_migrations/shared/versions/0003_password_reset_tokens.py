"""password_reset_tokens

Revision ID: 0003_password_reset_tokens
Revises: 0002_saved_filters_revoked_tokens
Create Date: 2026-05-29

Adds the password-reset token store (Roadmap 3.3). Only the SHA-256
hash of each token is stored; the raw token lives only in the reset
link sent to the user.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID

# Alembic identifiers
revision = "0003_password_reset_tokens"
down_revision = "0002_saved_filters_revoked_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"]
    )


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
