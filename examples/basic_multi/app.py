"""
Multi-tenant SaaS example — subdomain-based tenant resolution.

Run:
    uvicorn examples.basic_multi.app:app --reload --host 0.0.0.0

Then visit:
    http://127.0.0.1:8000/admin-ui           (superadmin / root panel)
    http://acme.localhost:8000/admin-ui      (tenant: acme)
    http://orbit.localhost:8000/admin-ui     (tenant: orbit)

*.localhost is resolved to 127.0.0.1 automatically on modern OSes.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

import examples.basic_multi.admin_config  # noqa: F401 — register admins

from adminfoundry import create_admin, CoreAdminConfig
from adminfoundry.extensions.observability import ObservabilityExtension
from adminfoundry.extensions.workflows import WorkflowsExtension
from examples.basic_multi.seed import seed, print_banner


config = CoreAdminConfig(
    database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./basic_multi.db"),
    secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
    enable_multi_tenant=True,
    tenant_resolution="subdomain",
    extensions=[WorkflowsExtension(), ObservabilityExtension()],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed()
    print_banner()
    yield


app = create_admin(
    config=config,
    title="adminfoundry — basic_multi",
    lifespan=lifespan,
)
