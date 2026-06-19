"""CLI smoke tests for ``tenant disable`` / ``tenant enable`` (plan §PR-8)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from asterion.cli.main import app as cli_app
from asterion.models.base import GlobalModel
from asterion.models.tenant import Tenant


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "tenant-lifecycle.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-tenant-lifecycle-secret")
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


def _engine_for(url: str):
    return create_async_engine(url, execution_options={"schema_translate_map": {"public": None}})


def _seed_tenant(url: str, *, slug: str = "acme", is_active: bool = True) -> Tenant:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                t = Tenant(
                    name=slug.title(),
                    slug=slug,
                    schema_name=f"tenant_{slug}",
                    is_active=is_active,
                )
                session.add(t)
            await session.refresh(t)
            return t

    return asyncio.run(_go())


def _read_tenant(url: str, slug: str) -> Tenant | None:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(Tenant).where(Tenant.slug == slug))
            return result.scalar_one_or_none()

    return asyncio.run(_go())


def _runner() -> CliRunner:
    return CliRunner()


# --- happy path ---


def test_tenant_disable_flips_is_active(env):
    _seed_tenant(env, slug="acme", is_active=True)
    result = _runner().invoke(cli_app, ["tenant", "disable", "acme"])
    assert result.exit_code == 0
    assert _read_tenant(env, "acme").is_active is False


def test_tenant_enable_flips_is_active(env):
    _seed_tenant(env, slug="acme", is_active=False)
    result = _runner().invoke(cli_app, ["tenant", "enable", "acme"])
    assert result.exit_code == 0
    assert _read_tenant(env, "acme").is_active is True


def test_tenant_disable_idempotent(env):
    _seed_tenant(env, slug="acme", is_active=False)
    result = _runner().invoke(cli_app, ["tenant", "disable", "acme"])
    assert result.exit_code == 0
    assert "already disabled" in result.output


def test_tenant_enable_idempotent(env):
    _seed_tenant(env, slug="acme", is_active=True)
    result = _runner().invoke(cli_app, ["tenant", "enable", "acme"])
    assert result.exit_code == 0
    assert "already enabled" in result.output


# --- error paths ---


def test_tenant_disable_unknown_slug(env):
    result = _runner().invoke(cli_app, ["tenant", "disable", "ghost"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_tenant_disable_invalid_slug(env):
    result = _runner().invoke(cli_app, ["tenant", "disable", "BAD SLUG"])
    assert result.exit_code != 0
