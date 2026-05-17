"""tenant_memberships

Revision ID: 0011_tenant_memberships
Revises: 0010_webhook_models
Create Date: 2026-05-17

- Create tenant_memberships table (user/tenant M2M with access state + profile fields)
- Create membership_roles table (tenant-scoped role assignments through membership)
- Data migration: existing user.tenant_id + user_roles → TenantMembership + membership_roles
- Remove tenant-scoped role entries from user_roles (they now live in membership_roles)
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_tenant_memberships"
down_revision = "0010_webhook_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )
    op.create_index(
        "ix_membership_tenant_active",
        "tenant_memberships",
        ["tenant_id", "is_active"],
    )

    op.create_table(
        "membership_roles",
        sa.Column(
            "membership_id",
            sa.String(36),
            sa.ForeignKey("tenant_memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Data migration — requires connection for raw SQL
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        # 1. Create TenantMembership for every user that has user.tenant_id set
        conn.execute(sa.text("""
            INSERT INTO tenant_memberships (id, user_id, tenant_id, is_active, created_at, updated_at)
            SELECT
                gen_random_uuid()::text,
                u.id,
                u.tenant_id,
                true,
                now(),
                now()
            FROM users u
            WHERE u.tenant_id IS NOT NULL
            ON CONFLICT (user_id, tenant_id) DO NOTHING
        """))

        # 2. For user_roles entries where the role is tenant-scoped, create membership_roles
        conn.execute(sa.text("""
            INSERT INTO membership_roles (membership_id, role_id)
            SELECT tm.id, ur.role_id
            FROM user_roles ur
            JOIN roles r ON ur.role_id = r.id
            JOIN tenant_memberships tm
                ON tm.user_id = ur.user_id AND tm.tenant_id = r.tenant_id
            WHERE r.tenant_id IS NOT NULL
            ON CONFLICT DO NOTHING
        """))

        # 3. Remove tenant-scoped entries from user_roles (now managed through membership_roles)
        conn.execute(sa.text("""
            DELETE FROM user_roles
            WHERE role_id IN (SELECT id FROM roles WHERE tenant_id IS NOT NULL)
        """))

    else:
        # SQLite: gen_random_uuid() not available; use Python uuid
        import uuid
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()

        users = conn.execute(
            sa.text("SELECT id, tenant_id FROM users WHERE tenant_id IS NOT NULL")
        ).fetchall()

        for row in users:
            mem_id = str(uuid.uuid4())
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO tenant_memberships "
                    "(id, user_id, tenant_id, is_active, created_at, updated_at) "
                    "VALUES (:id, :uid, :tid, 1, :now, :now)"
                ),
                {"id": mem_id, "uid": str(row[0]), "tid": str(row[1]), "now": now_iso},
            )

        # user_roles → membership_roles for tenant-scoped roles
        ur_rows = conn.execute(sa.text("""
            SELECT ur.user_id, ur.role_id, r.tenant_id
            FROM user_roles ur
            JOIN roles r ON ur.role_id = r.id
            WHERE r.tenant_id IS NOT NULL
        """)).fetchall()

        for ur_row in ur_rows:
            mem = conn.execute(
                sa.text(
                    "SELECT id FROM tenant_memberships "
                    "WHERE user_id = :uid AND tenant_id = :tid"
                ),
                {"uid": str(ur_row[0]), "tid": str(ur_row[2])},
            ).fetchone()
            if mem:
                conn.execute(
                    sa.text(
                        "INSERT OR IGNORE INTO membership_roles (membership_id, role_id) "
                        "VALUES (:mid, :rid)"
                    ),
                    {"mid": str(mem[0]), "rid": str(ur_row[1])},
                )

        conn.execute(sa.text("""
            DELETE FROM user_roles
            WHERE role_id IN (SELECT id FROM roles WHERE tenant_id IS NOT NULL)
        """))


def downgrade() -> None:
    # Restore tenant-scoped roles to user_roles from membership_roles before dropping tables
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT OR IGNORE INTO user_roles (user_id, role_id)
        SELECT tm.user_id, mr.role_id
        FROM membership_roles mr
        JOIN tenant_memberships tm ON mr.membership_id = tm.id
    """))

    op.drop_table("membership_roles")
    op.drop_index("ix_membership_tenant_active", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
