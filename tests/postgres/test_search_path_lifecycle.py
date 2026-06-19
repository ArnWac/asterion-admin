"""``SET LOCAL search_path`` lifecycle invariants.

The plan requires that the tenant search_path applies only inside the
current transaction and does NOT leak past commit or rollback. This is
critical: a leaked search_path would let the next request (running on a
recycled connection from the pool) accidentally read another tenant's
data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from asterion.tenancy.schema_strategy import set_search_path

pytestmark = pytest.mark.postgres


async def _current_search_path(session) -> str:
    result = await session.execute(text("SHOW search_path"))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_set_local_search_path_is_active_during_transaction(
    pg_schemas,
    pg_sessionmaker,
):
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            current = await _current_search_path(session)
            assert pg_schemas["a"] in current


@pytest.mark.asyncio
async def test_search_path_does_not_leak_after_commit(
    pg_schemas,
    pg_sessionmaker,
):
    """SET LOCAL is bound to the transaction. After commit, a new txn on
    the same session must see the default search_path again."""
    async with pg_sessionmaker() as session:
        async with session.begin():
            await set_search_path(session, pg_schemas["a"])
            assert pg_schemas["a"] in await _current_search_path(session)
        # txn committed; SET LOCAL value is gone.
        async with session.begin():
            current = await _current_search_path(session)
            assert pg_schemas["a"] not in current


@pytest.mark.asyncio
async def test_search_path_does_not_leak_after_rollback(
    pg_schemas,
    pg_sessionmaker,
):
    async with pg_sessionmaker() as session:
        try:
            async with session.begin():
                await set_search_path(session, pg_schemas["a"])
                assert pg_schemas["a"] in await _current_search_path(session)
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        async with session.begin():
            current = await _current_search_path(session)
            assert pg_schemas["a"] not in current


@pytest.mark.asyncio
async def test_search_path_does_not_leak_across_pooled_connections(
    pg_schemas,
    pg_sessionmaker,
):
    """A second session checked out from the same engine pool must NOT
    inherit the previous session's tenant search_path."""
    async with pg_sessionmaker() as session1:
        async with session1.begin():
            await set_search_path(session1, pg_schemas["a"])
            assert pg_schemas["a"] in await _current_search_path(session1)

    # Different session; if the pool returned the same underlying connection
    # the SET LOCAL must still be gone (it expires with the txn).
    async with pg_sessionmaker() as session2:
        async with session2.begin():
            current = await _current_search_path(session2)
            assert pg_schemas["a"] not in current
            assert pg_schemas["b"] not in current
