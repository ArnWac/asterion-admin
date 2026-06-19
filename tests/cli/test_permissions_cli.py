"""CLI smoke tests for ``asterion permissions ...`` commands."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from typer.testing import CliRunner

from asterion import CoreAdminConfig, create_admin
from asterion.cli.main import app as cli_app
from asterion.models.base import GlobalModel
from asterion.models.permission_catalog import PermissionCatalog
from asterion.registry import ModelAdmin


class _AppBase(DeclarativeBase):
    pass


class Post(_AppBase):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)


class PostAdmin(ModelAdmin):
    model = Post


def _make_app_module(db_url: str) -> str:
    """Create a synthetic module exposing ``app = create_admin(...)`` and
    return its dotted name so the CLI can import it."""
    module_name = "_asterion_test_app_for_permissions"
    module = types.ModuleType(module_name)

    fastapi_app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-secret-permissions",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(PostAdmin),
    )
    module.app = fastapi_app
    sys.modules[module_name] = module
    return f"{module_name}:app"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "permissions-cli.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-cli-secret-perms")
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "false")
    monkeypatch.setenv("ASTERION_ENABLE_BUILTIN_UI", "false")

    async def _setup():
        engine = create_async_engine(
            url,
            execution_options={"schema_translate_map": {"public": None}},
        )
        async with engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())
    return url


def _runner() -> CliRunner:
    return CliRunner()


def _read_catalog(url: str) -> list[str]:
    async def _go():
        engine = create_async_engine(
            url,
            execution_options={"schema_translate_map": {"public": None}},
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(PermissionCatalog.key))
            keys = list(result.scalars().all())
        await engine.dispose()
        return keys

    return asyncio.run(_go())


# --- check ---


def test_permissions_check_valid_key(env):
    result = _runner().invoke(cli_app, ["permissions", "check", "admin.users.list"])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_permissions_check_wildcard_key(env):
    result = _runner().invoke(cli_app, ["permissions", "check", "admin.users.*"])
    assert result.exit_code == 0


def test_permissions_check_rejects_invalid(env):
    result = _runner().invoke(cli_app, ["permissions", "check", "admin.*.list"])
    assert result.exit_code != 0
    assert "INVALID" in result.output


# --- sync ---


def test_permissions_sync_requires_app_spec(env, monkeypatch):
    monkeypatch.delenv("ASTERION_APP", raising=False)
    result = _runner().invoke(cli_app, ["permissions", "sync"])
    assert result.exit_code == 2


def test_permissions_sync_rejects_bad_spec(env):
    result = _runner().invoke(cli_app, ["permissions", "sync", "--app", "not_a_module_format"])
    assert result.exit_code != 0


def test_permissions_sync_populates_catalog(env):
    spec = _make_app_module(env)
    result = _runner().invoke(cli_app, ["permissions", "sync", "--app", spec])
    assert result.exit_code == 0, result.output
    assert "synced" in result.output.lower()

    keys = set(_read_catalog(env))
    # 5 CRUD keys for the registered PostAdmin
    assert "admin.posts.list" in keys
    assert "admin.posts.create" in keys
    assert "admin.posts.delete" in keys


def test_permissions_sync_idempotent(env):
    spec = _make_app_module(env)
    _runner().invoke(cli_app, ["permissions", "sync", "--app", spec])
    result = _runner().invoke(cli_app, ["permissions", "sync", "--app", spec])
    assert result.exit_code == 0
    # Output reports +0 added on second run
    assert "+0" in result.output


def test_permissions_sync_uses_env_var(env, monkeypatch):
    spec = _make_app_module(env)
    monkeypatch.setenv("ASTERION_APP", spec)
    result = _runner().invoke(cli_app, ["permissions", "sync"])
    assert result.exit_code == 0, result.output


# --- list ---


def test_permissions_list_empty(env):
    result = _runner().invoke(cli_app, ["permissions", "list"])
    assert result.exit_code == 0
    assert "No permission entries" in result.output


def test_permissions_list_after_sync(env):
    spec = _make_app_module(env)
    _runner().invoke(cli_app, ["permissions", "sync", "--app", spec])

    result = _runner().invoke(cli_app, ["permissions", "list"])
    assert result.exit_code == 0
    assert "admin.posts.list" in result.output


def test_permissions_list_source_filter(env):
    spec = _make_app_module(env)
    _runner().invoke(cli_app, ["permissions", "sync", "--app", spec])

    result_registry = _runner().invoke(cli_app, ["permissions", "list", "--source", "registry"])
    assert result_registry.exit_code == 0
    assert "admin.posts.list" in result_registry.output

    result_other = _runner().invoke(cli_app, ["permissions", "list", "--source", "other"])
    assert "No permission entries" in result_other.output
