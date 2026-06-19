"""Tests for DatabaseManager."""

from __future__ import annotations

import pytest
import pytest_asyncio

from asterion.db.session import DatabaseManager


@pytest_asyncio.fixture()
async def db():
    manager = DatabaseManager("sqlite+aiosqlite:///:memory:")
    yield manager
    await manager.dispose()


@pytest.mark.anyio
async def test_session_opens(db):
    from sqlalchemy import text

    async with db.session() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.anyio
async def test_separate_instances_are_independent():
    db1 = DatabaseManager("sqlite+aiosqlite:///:memory:")
    db2 = DatabaseManager("sqlite+aiosqlite:///:memory:")
    assert db1.engine is not db2.engine
    await db1.dispose()
    await db2.dispose()


@pytest.mark.anyio
async def test_dispose_cleans_up(db):
    await db.dispose()
    # second dispose should not raise
    await db.dispose()
