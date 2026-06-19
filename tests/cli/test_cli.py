"""Smoke tests for the asterion CLI.

We exercise the CLI through Typer's CliRunner against an in-memory SQLite
database. PostgreSQL-only paths (tenant schema provisioning) are skipped via
``--skip-schema``.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from asterion.cli.main import app
from asterion.models.base import GlobalModel
from asterion.models.tenant import Tenant
from asterion.models.user import User


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "asterion.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-cli-secret-key")
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "false")
    monkeypatch.setenv("ASTERION_ENABLE_BUILTIN_UI", "false")

    async def _setup_schema():
        engine = create_async_engine(
            url,
            execution_options={"schema_translate_map": {"public": None}},
        )
        async with engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup_schema())
    return url


@pytest.fixture
def runner():
    return CliRunner()


def _engine_for(url: str):
    return create_async_engine(
        url,
        execution_options={"schema_translate_map": {"public": None}},
    )


def _read_users(url: str) -> list[User]:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(User.__table__.select())
            rows = list(result.fetchall())
        await engine.dispose()
        return rows

    return asyncio.run(_go())


def _read_tenants(url: str) -> list[Tenant]:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(Tenant.__table__.select())
            rows = list(result.fetchall())
        await engine.dispose()
        return rows

    return asyncio.run(_go())


# --- doctor ---


def test_doctor_succeeds(env, runner):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_doctor_fails_with_missing_secret(env, runner, monkeypatch):
    monkeypatch.delenv("ASTERION_SECRET_KEY", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0


# --- create-superadmin ---


def test_create_superadmin_creates_user(env, runner):
    result = runner.invoke(
        app,
        ["create-superadmin", "--email", "admin@example.com"],
        input="hunter2-strong\nhunter2-strong\n",
    )
    assert result.exit_code == 0, result.output

    users = _read_users(env)
    assert len(users) == 1
    assert users[0].email == "admin@example.com"
    assert users[0].is_superadmin is True


def test_create_superadmin_promotes_existing(env, runner):
    runner.invoke(
        app,
        ["create-superadmin", "--email", "admin@example.com"],
        input="hunter2-strong\nhunter2-strong\n",
    )
    result = runner.invoke(
        app,
        ["create-superadmin", "--email", "admin@example.com"],
        input="hunter2-strong\nhunter2-strong\n",
    )
    assert result.exit_code == 0
    users = _read_users(env)
    assert len(users) == 1


# --- tenant create / list ---


def test_tenant_create_without_owner(env, runner):
    result = runner.invoke(
        app,
        ["tenant", "create", "--name", "Acme", "--slug", "acme", "--skip-schema"],
    )
    assert result.exit_code == 0, result.output
    tenants = _read_tenants(env)
    assert len(tenants) == 1
    assert tenants[0].slug == "acme"
    assert tenants[0].schema_name == "tenant_acme"


def test_tenant_create_rejects_invalid_slug(env, runner):
    result = runner.invoke(
        app,
        ["tenant", "create", "--name", "Bad", "--slug", "BAD SLUG", "--skip-schema"],
    )
    assert result.exit_code != 0
    assert "slug" in result.output.lower()


def test_tenant_create_is_idempotent(env, runner):
    runner.invoke(
        app,
        ["tenant", "create", "--name", "Acme", "--slug", "acme", "--skip-schema"],
    )
    result = runner.invoke(
        app,
        ["tenant", "create", "--name", "Acme", "--slug", "acme", "--skip-schema"],
    )
    assert result.exit_code == 0
    assert len(_read_tenants(env)) == 1


def test_tenant_create_with_existing_owner(env, runner):
    runner.invoke(
        app,
        ["create-superadmin", "--email", "owner@example.com"],
        input="hunter2-strong\nhunter2-strong\n",
    )
    result = runner.invoke(
        app,
        [
            "tenant",
            "create",
            "--name",
            "Acme",
            "--slug",
            "acme",
            "--owner-email",
            "owner@example.com",
            "--skip-schema",
        ],
    )
    assert result.exit_code == 0, result.output


def test_tenant_create_owner_email_unknown_without_create_owner(env, runner):
    result = runner.invoke(
        app,
        [
            "tenant",
            "create",
            "--name",
            "Acme",
            "--slug",
            "acme",
            "--owner-email",
            "ghost@example.com",
            "--skip-schema",
        ],
    )
    assert result.exit_code != 0
    assert "ghost@example.com" in result.output


def test_tenant_create_owner_with_create_owner_flag(env, runner):
    result = runner.invoke(
        app,
        [
            "tenant",
            "create",
            "--name",
            "Acme",
            "--slug",
            "acme",
            "--owner-email",
            "new-owner@example.com",
            "--create-owner",
            "--owner-password",
            "hunter2-strong",
            "--skip-schema",
        ],
    )
    assert result.exit_code == 0, result.output
    users = _read_users(env)
    emails = {u.email for u in users}
    assert "new-owner@example.com" in emails


def test_tenant_create_owner_create_without_password_fails(env, runner):
    result = runner.invoke(
        app,
        [
            "tenant",
            "create",
            "--name",
            "Acme",
            "--slug",
            "acme",
            "--owner-email",
            "x@example.com",
            "--create-owner",
            "--skip-schema",
        ],
    )
    assert result.exit_code != 0


def test_tenant_list_empty(env, runner):
    result = runner.invoke(app, ["tenant", "list"])
    assert result.exit_code == 0
    assert "No tenants" in result.output


def test_tenant_list_shows_tenants(env, runner):
    runner.invoke(
        app,
        ["tenant", "create", "--name", "Acme", "--slug", "acme", "--skip-schema"],
    )
    result = runner.invoke(app, ["tenant", "list"])
    assert result.exit_code == 0
    assert "acme" in result.output
    assert "tenant_acme" in result.output


def test_tenant_bootstrap_unknown_slug_fails(env, runner):
    result = runner.invoke(app, ["tenant", "bootstrap", "nonexistent"])
    assert result.exit_code != 0


def test_tenant_bootstrap_existing_tenant_succeeds(env, runner):
    runner.invoke(
        app,
        ["tenant", "create", "--name", "Acme", "--slug", "acme", "--skip-schema"],
    )
    # On SQLite, bootstrap is a no-op but should not fail.
    result = runner.invoke(app, ["tenant", "bootstrap", "acme"])
    assert result.exit_code == 0
