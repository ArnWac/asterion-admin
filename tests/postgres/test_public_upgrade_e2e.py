"""End-to-end provisioning against a *fresh* PostgreSQL database.

Regression test for the bug where ``asterion db upgrade-public`` failed on a
clean Postgres because a shared revision id was 33 chars and overflowed
``alembic_version.version_num VARCHAR(32)`` on the stamp — rolling the whole
upgrade back and leaving no ``public.tenants`` for ``tenant create`` /
``upgrade-tenant`` to build on.

The other postgres tests build the schema via ``metadata.create_all`` or run a
single tenant migration in isolation, so the full ``upgrade-public`` path from
base to head was never exercised on real Postgres. This runs it for real, then
the tenant provisioning that depends on it.

Runs only when ``ASTERION_TEST_POSTGRES_URL`` is set (see conftest). Creates and
drops its own throwaway database so it neither sees nor disturbs the shared
public schema other postgres tests use.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from asterion.cli import app
from asterion.db.alembic_support import bundled_migrations_path

pytestmark = pytest.mark.postgres

runner = CliRunner()


def _shared_head() -> str:
    cfg = Config()
    cfg.set_main_option("script_location", str(bundled_migrations_path("shared")))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"expected a single shared head, got {heads}"
    return heads[0]


async def _exec(url: str, sql: str, autocommit: bool = False):
    """Run a statement and return scalar rows. AUTOCOMMIT for CREATE/DROP DATABASE."""
    engine = create_async_engine(
        url, isolation_level="AUTOCOMMIT" if autocommit else "READ COMMITTED"
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            try:
                return result.scalars().all()
            except Exception:
                return None
    finally:
        await engine.dispose()


def _table_names(url: str, schema: str) -> set[str]:
    rows = asyncio.run(
        _exec(
            url,
            f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'",
        )
    )
    return set(rows or [])


def test_fresh_public_upgrade_then_tenant_provisioning(monkeypatch):
    admin_url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    base = make_url(admin_url)
    db_name = f"asterion_e2e_{uuid.uuid4().hex[:12]}"
    fresh_url = base.set(database=db_name).render_as_string(hide_password=False)

    # A genuinely empty database — the exact condition the bug needed.
    asyncio.run(_exec(admin_url, f'CREATE DATABASE "{db_name}"', autocommit=True))
    try:
        monkeypatch.setenv("ASTERION_DATABASE_URL", fresh_url)
        monkeypatch.setenv("ASTERION_SECRET_KEY", "e2e-test-secret-not-the-default")

        # 1. base -> head on the shared/public schema. This is what overflowed.
        result = runner.invoke(app, ["db", "upgrade-public"])
        assert result.exit_code == 0, result.output

        public_tables = _table_names(fresh_url, "public")
        assert {"users", "tenants", "audit_logs", "revoked_tokens"}.issubset(public_tables), (
            public_tables
        )

        # The version actually persisted (transaction committed, not rolled back)
        # and matches head.
        version = asyncio.run(_exec(fresh_url, "SELECT version_num FROM public.alembic_version"))
        assert version == [_shared_head()], version

        # 2. tenant create now has public.tenants to write to, and provisions the
        #    tenant schema + its alembic_version.
        result = runner.invoke(
            app,
            [
                "tenant",
                "create",
                "--name",
                "Demo",
                "--slug",
                "demo",
                "--owner-email",
                "owner@example.com",
                "--create-owner",
                "--owner-password",
                "owner-pass-12345",
            ],
        )
        assert result.exit_code == 0, result.output

        # 3. upgrade-tenant resolves the schema and applies the tenant tree.
        result = runner.invoke(app, ["db", "upgrade-tenant", "demo"])
        assert result.exit_code == 0, result.output

        tenant_tables = _table_names(fresh_url, "tenant_demo")
        assert {
            "tenant_roles",
            "tenant_role_permissions",
            "tenant_membership_roles",
            "alembic_version",
        }.issubset(tenant_tables), tenant_tables
    finally:
        # Terminate stray connections, then drop the throwaway database.
        asyncio.run(
            _exec(
                admin_url,
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()",
            )
        )
        asyncio.run(_exec(admin_url, f'DROP DATABASE IF EXISTS "{db_name}"', autocommit=True))
