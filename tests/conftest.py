"""Shared fixtures for the asterion test suite.

Uses SQLite async (aiosqlite) for fast in-process tests.
PostgreSQL-specific behaviour (schema search_path) is unit-tested via
SQL-generation helpers rather than requiring a real Postgres instance.
"""

from __future__ import annotations

import gc

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from asterion.models.base import GlobalModel

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_lingering_async_engines():
    """Dispose every still-live ``AsyncEngine`` at session end, on a live loop.

    Many tests build an app via ``create_admin(...)`` (which constructs a
    ``DatabaseManager`` → ``create_async_engine``) without disposing it. With the
    session-scoped event loop, those undisposed engines' aiosqlite connections
    are finalised when the loop is torn down — and on Linux that deterministically
    raises ``RuntimeError: Event loop is closed`` during loop teardown, failing
    the whole run even though every test passed (Windows tolerates it).

    Running here — after all tests but while the session loop is still alive —
    closes those connections cleanly. ``dispose()`` is idempotent, so engines that
    already disposed in their own fixtures are unaffected.
    """
    yield
    gc.collect()
    for obj in list(gc.get_objects()):
        if isinstance(obj, AsyncEngine):
            try:
                await obj.dispose()
            except Exception:
                # Best-effort cleanup — never fail teardown over a stray engine.
                pass


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
