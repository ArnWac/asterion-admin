"""saved_filters + revoked_tokens

Revision ID: 0002_saved_filters_revoked_tokens
Revises: 0001_initial_public
Create Date: 2026-05-29

Adds two global tables introduced after the initial v1 cut:

  saved_filters   — per-user saved list-view filter configs (D2)
  revoked_tokens  — single-token (per-jti) revocation store (3.2)

Both were originally only created via ``Base.metadata.create_all`` in
tests; this migration brings the Alembic path in line so production
deployments (which run ``alembic upgrade``) actually get the tables.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from asterion.models.base import GUID

# Alembic identifiers
revision = "0002_saved_filters_revoked_tokens"
down_revision = "0001_initial_public"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_filters",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("resource", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
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
        "ix_saved_filters_owner", "saved_filters", ["user_id", "resource"]
    )

    op.create_table(
        "revoked_tokens",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("jti", sa.String(100), nullable=False),
        sa.Column("user_id", GUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(50), nullable=True),
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
        sa.UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
    )
    op.create_index("ix_revoked_tokens_jti", "revoked_tokens", ["jti"], unique=True)
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("revoked_tokens")
    op.drop_table("saved_filters")
