"""Service-account permission resolution against a real tenant schema.

Proves the acceptance criterion that SQLite can't: a token minted for a
service account resolves — through the real ``SET LOCAL search_path`` tenant
RBAC lookup — to a principal carrying EXACTLY the granted permission keys.

Runs only when ``ASTERION_TEST_POSTGRES_URL`` is set (see conftest).
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from asterion.auth.service_accounts import create_service_account
from asterion.models.tenant import Tenant
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.providers.permissions import BuiltinPermissionProvider
from asterion.tenancy.schema_strategy import set_search_path

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_service_account_permissions_resolve(pg_engine, pg_schemas, pg_sessionmaker):
    url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    schema = pg_schemas["a"]
    keys = ["admin.time_entries.create", "admin.time_entries.read"]

    async with pg_sessionmaker() as session:
        async with session.begin():
            tenant = Tenant(
                name="A",
                slug=f"svc{uuid.uuid4().hex[:8]}",
                schema_name=schema,
                is_active=True,
            )
            session.add(tenant)
            await session.flush()
            tenant_id = tenant.id
            tenant_slug = tenant.slug

            # RBAC tables are tenant-local — scope the session.
            await set_search_path(session, schema)
            user = await create_service_account(
                session,
                tenant_id=tenant_id,
                label="terminal",
                permission_keys=keys,
            )
            user_id = user.id

    # Resolve permissions exactly as the framework does for an incoming request:
    # the provider builds its own session from runtime.db.engine and applies the
    # tenant search_path. A minimal runtime stand-in is enough.
    provider = BuiltinPermissionProvider()
    runtime = SimpleNamespace(
        config=SimpleNamespace(database_url=url),
        db=SimpleNamespace(engine=pg_engine),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(asterion=runtime)))
    principal = AdminPrincipal(id=str(user_id), is_superadmin=False)
    tenant_obj = AdminTenant(id=str(tenant_id), slug=tenant_slug, schema_name=schema)

    perms = await provider.get_permissions(principal, tenant_obj, request=request)
    assert perms == frozenset(keys)
