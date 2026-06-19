"""Tests for the enhanced ``asterion doctor`` command (plan §PR-10)."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.cli.main import app as cli_app
from asterion.models.base import GlobalModel
from asterion.models.permission_catalog import PermissionCatalog


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "doctor.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-doctor-secret")
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "false")
    monkeypatch.setenv("ASTERION_ENABLE_BUILTIN_UI", "false")

    async def _setup():
        engine = create_async_engine(
            url, execution_options={"schema_translate_map": {"public": None}}
        )
        async with engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())
    return url


def _seed_catalog(url: str, count: int = 3) -> None:
    async def _go():
        engine = create_async_engine(
            url, execution_options={"schema_translate_map": {"public": None}}
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                for i in range(count):
                    session.add(
                        PermissionCatalog(
                            key=f"admin.x{i}.list",
                            category=f"x{i}",
                            source="test",
                        )
                    )
        await engine.dispose()

    asyncio.run(_go())


# --- baseline ---


def test_doctor_succeeds_with_minimal_env(env):
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "[CFG] OK" in result.output
    assert "[DB]  OK" in result.output
    assert "[VER] OK" in result.output
    assert "doctor: all checks passed" in result.output


def test_doctor_reports_environment_from_config(env, monkeypatch):
    monkeypatch.setenv("ASTERION_ENVIRONMENT", "development")
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "environment=development" in result.output


# --- PermissionCatalog ---


def test_doctor_warns_when_catalog_empty(env):
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[CAT] WARN" in result.output
    assert "PermissionCatalog is empty" in result.output


def test_doctor_ok_when_catalog_populated(env):
    _seed_catalog(env, count=5)
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[CAT] OK (5 permission key(s))" in result.output


# --- multi-tenant ---


def test_doctor_reports_multi_tenant_disabled(env):
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[MT]  OK (disabled)" in result.output


def test_doctor_warns_multi_tenant_on_sqlite(env, monkeypatch):
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "true")
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[MT]  WARN" in result.output
    assert "PostgreSQL" in result.output


# --- CORS ---


def test_doctor_reports_no_cors_origins(env):
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[CORS] OK (middleware not installed" in result.output


def test_doctor_reports_configured_cors(env, monkeypatch):
    monkeypatch.setenv("ASTERION_CORS_ORIGINS", "https://app.example.com,https://api.example.com")
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[CORS] OK (2 origin(s))" in result.output


# --- registry --app option ---


def test_doctor_skips_registry_check_without_app_spec(env):
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert "[REG] SKIP" in result.output


def test_doctor_reports_registered_resources_with_app(env, monkeypatch):
    # Build a synthetic module exposing a real asterion FastAPI app.
    from sqlalchemy import Column, Integer, String
    from sqlalchemy.orm import DeclarativeBase

    class _Base(DeclarativeBase):
        pass

    class Widget(_Base):
        __tablename__ = "widgets"
        id = Column(Integer, primary_key=True)
        name = Column(String(200), nullable=False)

    class WidgetAdmin(ModelAdmin):
        model = Widget

    module = types.ModuleType("_doctor_probe_app")
    module.app = create_admin(
        config=CoreAdminConfig(
            database_url=env,
            secret_key="test-doctor-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda r: r.register(WidgetAdmin),
    )
    sys.modules["_doctor_probe_app"] = module

    result = CliRunner().invoke(cli_app, ["doctor", "--app", "_doctor_probe_app:app"])
    assert result.exit_code == 0, result.output
    assert "[REG] OK (1 resource(s)" in result.output
    assert "widgets" in result.output


# --- failure mode ---


def test_doctor_exits_nonzero_when_db_unreachable(tmp_path, monkeypatch):
    bad_url = f"sqlite+aiosqlite:///{tmp_path}/no/such/dir/x.db"
    monkeypatch.setenv("ASTERION_DATABASE_URL", bad_url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-doctor-bad-secret")
    monkeypatch.setenv("ASTERION_ENABLE_MULTI_TENANT", "false")
    monkeypatch.setenv("ASTERION_ENABLE_BUILTIN_UI", "false")

    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code != 0
    assert "FAIL" in result.output
