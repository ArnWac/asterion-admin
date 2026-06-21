from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.tenancy.schema_strategy import set_search_path


async def get_async_session(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """Return one request-scoped AsyncSession inside one transaction.

    On PostgreSQL, when the request resolved a tenant (``request.state.tenant``
    set by ``TenantMiddleware``), the session's ``search_path`` is pointed at
    that tenant's schema for the whole transaction. Tenant-local
    (``TenantModel``) tables are unqualified, so they resolve inside the tenant
    schema; global (``GlobalModel``) tables carry an explicit ``public.``
    qualifier and are unaffected. ``SET LOCAL`` is transaction-scoped and
    evaporates on commit/rollback, so no tenant state leaks onto the next
    request that reuses the pooled connection.

    This is the single place that scopes the CRUD query path — CRUD, contract,
    actions and import/export all depend on this session.
    """
    runtime = request.app.state.asterion

    async with runtime.db.session() as session:
        async with session.begin():
            tenant = getattr(request.state, "tenant", None)
            if tenant is not None and "postgresql" in runtime.config.database_url:
                await set_search_path(session, tenant.schema_name)
            # Expose the request-scoped session so notifiers / extensions can
            # join this transaction (e.g. the email outbox writes its row in the
            # same transaction as the invite/user that triggered it). Neutral
            # hook — nothing in core depends on it being read.
            request.state.db_session = session
            yield session
