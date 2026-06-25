"""The bundled tenant migration actually creates tables in the tenant schema.

Regression test for the env.py bug where `_run_tenant_migrations` logged
"Running upgrade" but persisted nothing: the `SET search_path` before
Alembic's `begin_transaction()` autobegan a transaction that the outer
`connect()` context rolled back. The other postgres tests build tenant tables
via `TenantBase.metadata.create_all`, and the bootstrap unit test mocks
`command.upgrade` — so the real online migration path was uncovered. This runs
it for real.

Runs only when ``ASTERION_TEST_POSTGRES_URL`` is set (see conftest).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from asterion.tenancy.bootstrap import _run_tenant_migrations

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_run_tenant_migrations_persists_tables(pg_engine):
    url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    schema = f"tenant_mig_{uuid.uuid4().hex[:8]}"

    async with pg_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        await _run_tenant_migrations(schema, database_url=url)

        async with pg_engine.connect() as conn:
            names = set(
                (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = :s"
                        ),
                        {"s": schema},
                    )
                )
                .scalars()
                .all()
            )
        # The framework tenant tables AND the framework's own per-schema version
        # table must actually persist in the tenant schema (not silently roll
        # back). Theme H: the framework base is tracked in
        # ``alembic_version_asterion_tenant`` (the default ``alembic_version`` is
        # owned by the app tree, if any).
        assert {
            "tenant_roles",
            "tenant_role_permissions",
            "tenant_membership_roles",
            "tenant_audit_logs",
            "alembic_version_asterion_tenant",
        }.issubset(names), f"missing tenant tables in {schema}: {names}"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
