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
from contextlib import asynccontextmanager

import examples.basic_multi.database  # noqa: F401 — set DATABASE_URL before other imports

from fastapi import FastAPI

import examples.basic_multi.admin_config  # noqa: F401 — register admins
from adminfoundry import create_admin, CoreAdminConfig
from adminfoundry.extensions.workflows import WorkflowsExtension
from adminfoundry.settings import settings
from examples.basic_multi.seed import seed, print_banner


config = CoreAdminConfig.from_settings(settings)
config.enable_multi_tenant = True
config.tenant_resolution = "subdomain"
config.extensions.append(WorkflowsExtension())


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
