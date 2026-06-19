"""CLI tests for ``asterion audit prune`` (plan §PR-10)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from asterion.cli.main import app as cli_app
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "audit-prune.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("ASTERION_DATABASE_URL", url)
    monkeypatch.setenv("ASTERION_SECRET_KEY", "test-audit-prune-secret")
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


def _seed_audit_row(url: str, *, days_old: int) -> None:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                created = datetime.now(UTC) - timedelta(days=days_old)
                row = AuditLog(
                    method="POST",
                    path="/x",
                    status_code=200,
                    action="probe",
                    created_at=created,
                    updated_at=created,
                )
                session.add(row)
        await engine.dispose()

    asyncio.run(_go())


def _count_audit_rows(url: str) -> int:
    async def _go():
        engine = _engine_for(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            from sqlalchemy import func, select

            result = await session.execute(select(func.count(AuditLog.id)))
            value = result.scalar_one()
        await engine.dispose()
        return value

    return asyncio.run(_go())


def test_prune_deletes_rows_older_than_threshold(env):
    _seed_audit_row(env, days_old=120)
    _seed_audit_row(env, days_old=10)
    assert _count_audit_rows(env) == 2

    result = CliRunner().invoke(cli_app, ["audit", "prune", "--days", "90", "--yes"])
    assert result.exit_code == 0
    assert "Pruned 1" in result.output
    assert _count_audit_rows(env) == 1


def test_prune_zero_rows_when_all_recent(env):
    _seed_audit_row(env, days_old=10)
    result = CliRunner().invoke(cli_app, ["audit", "prune", "--days", "90", "--yes"])
    assert result.exit_code == 0
    assert "Pruned 0" in result.output
    assert _count_audit_rows(env) == 1


def test_prune_requires_confirmation_or_yes_flag(env):
    _seed_audit_row(env, days_old=120)
    # Send "n" to the confirmation prompt
    result = CliRunner().invoke(cli_app, ["audit", "prune", "--days", "90"], input="n\n")
    assert result.exit_code != 0
    # No rows deleted
    assert _count_audit_rows(env) == 1


def test_prune_rejects_zero_or_negative_days(env):
    result = CliRunner().invoke(cli_app, ["audit", "prune", "--days", "0", "--yes"])
    assert result.exit_code != 0
