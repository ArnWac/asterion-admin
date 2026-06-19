"""Shared fixtures for the PostgreSQL integration test suite.

These tests prove the schema-per-tenant isolation invariants that SQLite
cannot reproduce. They run only when ``ASTERION_TEST_POSTGRES_URL`` is
set in the environment. Locally::

    docker-compose up -d db
    export ASTERION_TEST_POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asterion
    pytest -m postgres

Each test creates its own pair of tenant schemas with a unique suffix so
the tests can run in parallel and clean up after themselves.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion.models.base import GlobalModel, TenantBase

POSTGRES_URL_ENV = "ASTERION_TEST_POSTGRES_URL"


def _postgres_url() -> str | None:
    return os.environ.get(POSTGRES_URL_ENV)


def pytest_collection_modifyitems(config, items):
    """Apply the ``postgres`` marker + skip-reason to everything in this dir."""
    url = _postgres_url()
    if url:
        return
    skip = pytest.mark.skip(
        reason=f"Set {POSTGRES_URL_ENV} to run real PostgreSQL integration tests."
    )
    for item in items:
        if "tests/postgres" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest_asyncio.fixture
async def pg_engine():
    """Yield an AsyncEngine bound to the Postgres test URL."""
    url = _postgres_url()
    if url is None:
        pytest.skip(f"Set {POSTGRES_URL_ENV} to run.")

    engine = create_async_engine(url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_schemas(pg_engine):
    """Create two fresh tenant schemas with the v1 tenant tables in each.

    Yields ``{"a": "tenant_pra_<uuid>", "b": "tenant_prb_<uuid>"}``. The
    public schema also receives the global tables so foreign-key targets
    (e.g. ``tenant_memberships.tenant_id`` → ``public.tenants.id``) resolve.
    """
    suffix = uuid.uuid4().hex[:8]
    schema_a = f"tenant_pra_{suffix}"
    schema_b = f"tenant_prb_{suffix}"

    async with pg_engine.begin() as conn:
        # Public/global tables
        await conn.run_sync(GlobalModel.metadata.create_all)

        # Tenant A schema + tenant tables
        await conn.execute(text(f'CREATE SCHEMA "{schema_a}"'))
        await conn.execute(text(f'SET LOCAL search_path TO "{schema_a}"'))
        await conn.run_sync(TenantBase.metadata.create_all)

        # Tenant B schema + tenant tables
        await conn.execute(text(f'CREATE SCHEMA "{schema_b}"'))
        await conn.execute(text(f'SET LOCAL search_path TO "{schema_b}"'))
        await conn.run_sync(TenantBase.metadata.create_all)

    try:
        yield {"a": schema_a, "b": schema_b}
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_a}" CASCADE'))
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_b}" CASCADE'))
            # Best-effort cleanup of global tables. If concurrent tests share
            # the same URL, leave the public schema alone.
            try:
                await conn.run_sync(GlobalModel.metadata.drop_all)
            except Exception:
                pass


@pytest_asyncio.fixture
async def pg_sessionmaker(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False)
