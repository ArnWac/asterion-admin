"""Verify that the v1 initial Alembic migrations apply cleanly on SQLite.

These tests prove that ``alembic upgrade head`` against a fresh database
creates every table the runtime needs — without ever touching
``Base.metadata.create_all``. PostgreSQL deployments take the same path
in production; SQLite is used here only because it spins up in
milliseconds.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _public_cfg(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic_shared.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location", str(PROJECT_ROOT / "asterion" / "_migrations" / "shared")
    )
    return cfg


def _tenant_cfg(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic_tenant.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location", str(PROJECT_ROOT / "asterion" / "_migrations" / "tenant")
    )
    return cfg


def _sync_url_from_path(db_path: Path) -> str:
    """Alembic's env.py uses async drivers — but for upgrade tests we want
    the sync driver to introspect the resulting schema. Return the sync URL."""
    return f"sqlite:///{db_path}"


def _async_url_from_path(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _list_tables(db_path: Path) -> set[str]:
    engine = create_engine(_sync_url_from_path(db_path))
    try:
        inspector = inspect(engine)
        return set(inspector.get_table_names())
    finally:
        engine.dispose()


# --- public schema migration ---


def test_public_upgrade_creates_all_global_tables(tmp_path):
    db_path = tmp_path / "public.db"
    cfg = _public_cfg(_async_url_from_path(db_path))

    command.upgrade(cfg, "head")

    tables = _list_tables(db_path)
    assert {
        "alembic_version",
        "users",
        "tenants",
        "tenant_memberships",
        "permission_catalog",
        "audit_logs",
        "impersonation_logs",
        # 0002 — added after the initial cut
        "saved_filters",
        "revoked_tokens",
        # 0003
        "password_reset_tokens",
        # 0004
        "two_factor_backup_codes",
    }.issubset(tables), f"Missing tables. Got: {tables}"


def test_public_upgrade_is_idempotent(tmp_path):
    db_path = tmp_path / "public.db"
    cfg = _public_cfg(_async_url_from_path(db_path))
    command.upgrade(cfg, "head")
    # Second upgrade is a no-op
    command.upgrade(cfg, "head")
    tables = _list_tables(db_path)
    assert "users" in tables


def test_public_downgrade_drops_all_global_tables(tmp_path):
    db_path = tmp_path / "public.db"
    cfg = _public_cfg(_async_url_from_path(db_path))
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "base")
    tables = _list_tables(db_path)
    assert "users" not in tables
    assert "tenants" not in tables


def test_public_users_indexes_present(tmp_path):
    """email index is critical for login lookup."""
    db_path = tmp_path / "public.db"
    cfg = _public_cfg(_async_url_from_path(db_path))
    command.upgrade(cfg, "head")

    engine = create_engine(_sync_url_from_path(db_path))
    try:
        inspector = inspect(engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("users")}
        assert "ix_users_email" in indexes
    finally:
        engine.dispose()


# --- tenant schema migration ---


def test_tenant_upgrade_creates_tenant_rbac_tables(tmp_path):
    db_path = tmp_path / "tenant.db"
    cfg = _tenant_cfg(_async_url_from_path(db_path))

    command.upgrade(cfg, "head")

    tables = _list_tables(db_path)
    assert {
        "alembic_version",
        "tenant_roles",
        "tenant_role_permissions",
        "tenant_membership_roles",
    }.issubset(tables), f"Missing tables. Got: {tables}"


def test_tenant_upgrade_is_idempotent(tmp_path):
    db_path = tmp_path / "tenant.db"
    cfg = _tenant_cfg(_async_url_from_path(db_path))
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    assert "tenant_roles" in _list_tables(db_path)


def test_tenant_downgrade_drops_tables(tmp_path):
    db_path = tmp_path / "tenant.db"
    cfg = _tenant_cfg(_async_url_from_path(db_path))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    tables = _list_tables(db_path)
    assert "tenant_roles" not in tables


# Note: we deliberately don't test "public + tenant on the same DB file"
# because in production they live in DIFFERENT PostgreSQL schemas, so each
# has its own ``alembic_version`` table. On SQLite (one namespace) the two
# alembic_version rows would collide, which is a test-environment artifact
# not a real deployment concern.
