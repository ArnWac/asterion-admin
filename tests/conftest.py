"""Shared fixtures for the asterion test suite.

Uses SQLite async (aiosqlite) for fast in-process tests.
PostgreSQL-specific behaviour (schema search_path) is unit-tested via
SQL-generation helpers rather than requiring a real Postgres instance.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.models.base import GlobalModel

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def engine():
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        async with s.begin():
            yield s
