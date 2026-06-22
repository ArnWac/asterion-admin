"""CLI smoke tests for ``asterion service-account create``.

Runs on SQLite: the command skips ``SET LOCAL search_path`` for non-PostgreSQL
URLs (mirroring ``get_async_session``), so the tenant-local RBAC rows land in
the single SQLite namespace alongside the global tables.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from asterion.cli.main import app as cli_app
from asterion.models.base import GLOBAL_METADATA, TenantBase
from asterion.models.tenant import Tenant
from asterion.models.user import User


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'svc-cli.db'}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-svc-cli-secret-key")
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "false")
    monkeypatch.setenv("ASTERION_ENABLE_BUILTIN_UI", "false")

    async def _setup():
        engine = create_async_engine(
            url, execution_options={"schema_translate_map": {"public": None}}
        )
        async with engine.begin() as conn:
            await conn.run_sync(GLOBAL_METADATA.create_all)
            await conn.run_sync(TenantBase.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            async with s.begin():
                s.add(Tenant(name="Acme", slug="acme", schema_name="tenant_acme", is_active=True))
        await engine.dispose()

    asyncio.run(_setup())
    return url


def _users(url: str) -> list[User]:
    async def _go():
        engine = create_async_engine(
            url, execution_options={"schema_translate_map": {"public": None}}
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            rows = (await s.execute(select(User))).scalars().all()
        await engine.dispose()
        return rows

    return asyncio.run(_go())


def test_create_prints_token_and_provisions_user(env):
    result = CliRunner().invoke(
        cli_app,
        [
            "service-account",
            "create",
            "--tenant",
            "acme",
            "--label",
            "term",
            "--permission",
            "admin.time_entries.create",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Access token" in result.output
    assert "Service account created" in result.output

    users = _users(env)
    assert len(users) == 1
    assert users[0].is_active is True
    assert users[0].is_superadmin is False


def test_create_unknown_tenant_errors(env):
    result = CliRunner().invoke(
        cli_app,
        ["service-account", "create", "--tenant", "ghost", "--label", "x"],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_create_invalid_permission_key_errors(env):
    result = CliRunner().invoke(
        cli_app,
        [
            "service-account",
            "create",
            "--tenant",
            "acme",
            "--label",
            "x",
            "--permission",
            "NOT A KEY",
        ],
    )
    assert result.exit_code != 0
    assert "Cannot create service account" in result.output
