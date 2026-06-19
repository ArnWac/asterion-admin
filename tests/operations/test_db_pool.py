"""Tests for the configurable DB pool plumbing."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from asterion import CoreAdminConfig
from asterion.db.session import DatabaseManager


def test_pool_kwargs_passed_to_postgres_engine():
    captured: dict = {}

    def _spy(url, **kw):
        captured["url"] = url
        captured.update(kw)
        # Return a real (lazy) async engine to satisfy DatabaseManager init
        from sqlalchemy.ext.asyncio import create_async_engine

        return create_async_engine(url, **kw)

    with patch("asterion.db.session.create_async_engine", side_effect=_spy):
        DatabaseManager(
            "postgresql+asyncpg://postgres:postgres@localhost:5432/x",
            pool_size=42,
            max_overflow=7,
            pool_pre_ping=False,
        )

    assert captured["pool_size"] == 42
    assert captured["max_overflow"] == 7
    assert captured["pool_pre_ping"] is False


def test_pool_kwargs_NOT_forwarded_for_sqlite():
    """SQLite uses NullPool / no pool_size. Forwarding pool_size would be
    silently ignored by SQLAlchemy but it should not be passed at all to
    keep the call explicit."""
    captured: dict = {}

    def _spy(url, **kw):
        captured["url"] = url
        captured.update(kw)
        from sqlalchemy.ext.asyncio import create_async_engine

        return create_async_engine(url, **kw)

    with patch("asterion.db.session.create_async_engine", side_effect=_spy):
        DatabaseManager(
            "sqlite+aiosqlite:///:memory:",
            pool_size=99,
            max_overflow=99,
        )

    assert "pool_size" not in captured
    assert "max_overflow" not in captured


def test_config_pool_fields_validate():
    with pytest.raises(ValueError, match="db_pool_size"):
        CoreAdminConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            secret_key="x" * 32,
            db_pool_size=0,
        ).validate()

    with pytest.raises(ValueError, match="db_max_overflow"):
        CoreAdminConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            secret_key="x" * 32,
            db_max_overflow=-1,
        ).validate()


def test_config_pool_defaults():
    cfg = CoreAdminConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="x" * 32,
    )
    assert cfg.db_pool_size == 10
    assert cfg.db_max_overflow == 20
    assert cfg.db_pool_pre_ping is True
